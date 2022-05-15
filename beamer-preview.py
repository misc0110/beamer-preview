#!/usr/bin/python3
import sys
import re
import warnings
import os
import logging
import colorlog
import argparse
import time
import multiprocessing
import hashlib
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from subprocess import Popen, STDOUT, PIPE
from PyPDF2 import PdfFileMerger
from pylatexenc import macrospec
from pylatexenc.latexwalker import (LatexWalker, LatexEnvironmentNode,
                                    LatexGroupNode, LatexMacroNode,
                                    LatexWalkerParseError, LatexCharsNode,
                                    get_default_latex_context_db)

logger = None
args = None


def init_logging():
    global logger
    formatter = colorlog.ColoredFormatter(
        "%(log_color)s[%(levelname)-8s]%(reset)s %(log_color)s%(message)s%(reset)s",
        datefmt=None,
        reset=True,
        log_colors={
                'DEBUG':    'cyan',
                'INFO':     'green',
                'WARNING':  'yellow',
                'ERROR':    'red',
                'CRITICAL': 'red',
        },
        secondary_log_colors={},
        style='%'
    )
    handler = colorlog.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    logger = colorlog.getLogger('slide-preview')
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def init_parser():
    parser = argparse.ArgumentParser(description="Recompile only changed TeX slides.")
    parser.add_argument("--out", "-o", nargs="?", default="preview.pdf")
    parser.add_argument("--compiler", "-c", nargs="?", default="pdflatex")
    parser.add_argument("--ignore-errors", "-i", action="store_true", dest="ignore_errors", default=False)
    parser.add_argument("--prefix", "-p", nargs="?", default="beamer.out")
    parser.add_argument("--force", action="store_true", default=False)
    parser.add_argument("--watch", action="store_true", default=False)
    parser.add_argument("--smp", nargs="?", type=int, default=multiprocessing.cpu_count())
    parser.add_argument("--compiler-option", nargs="*", default=[], dest="compiler_option")
    parser.add_argument("--runs", "-r", nargs='?', default=1)
    parser.add_argument("slides")

    return parser


def slide_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def has_changed(content, fname, pdf):
    try:
        diff = content != open(fname).read()
        if diff:
            return True
        if not os.path.exists(pdf):
            return True
    except:
        return True
    return False


class AbortException(Exception):
    pass


def fatal(msg):
    logger.critical(msg)
    raise AbortException()


def error(msg, exception=None):
    logger.error(msg)
    if not args.ignore_errors:
        if not exception:
            raise AbortException()


class EnvironmentRawParser(macrospec.MacroStandardArgsParser):
    def __init__(self, environment_name, **kwargs):
        super(EnvironmentRawParser, self).__init__(argspec='{', **kwargs)
        self.environment_name = environment_name

    def parse_args(self, w, pos, parsing_state=None):
        endverbpos = w.s.find(r'\end{' + self.environment_name + '}', pos)
        if endverbpos == -1:
            raise LatexWalkerParseError(
                s=w.s,
                pos=pos,
                msg=r"Cannot find matching \end{%s}" % (self.environment_name)
            )
        len_ = endverbpos-pos

        argd = macrospec.ParsedVerbatimArgs(
            verbatim_chars_node=w.make_node(LatexCharsNode,
                                            parsing_state=parsing_state,
                                            chars=w.s[pos:pos+len_],
                                            pos=pos,
                                            len=len_)
        )
        return (argd, pos, len_)

    def __repr__(self):
        return '{}(environment_name={!r})'.format(
            self.__class__.__name__, self.environment_name
        )


def parse_slides(tex):
    tex_string = "\n".join(tex)

    latex_context = get_default_latex_context_db()
    print(latex_context)
    latex_context.add_context_category('code', prepend=True, environments=[
        macrospec.EnvironmentSpec('lstlisting', args_parser=EnvironmentRawParser('lstlisting'))
    ], macros=[])

    w = LatexWalker(tex_string, latex_context=latex_context)

    # find header
    begin_document_string = "\\begin{document}"
    document_index = tex_string.find(begin_document_string)
    (nodelist, pos, len_) = w.get_latex_nodes(pos=document_index)
    header_node = nodelist[0]
    header = tex_string[0:header_node.pos]
    header += begin_document_string

    slides = [""]
    footer = ""

    # get other nodes
    (nodelist, pos, len_) = w.get_latex_nodes(pos=header_node.pos + len(begin_document_string))
    slide_idx = 0

    # own-slide macros
    single_slide_macros = [
        'section',
        'maketitle',
        'imageslide',
        'fullFrameMovie',
        'fullFrameImage',
        'fullFrameImageZoomed'
    ]

    # cache macros that are added inbetween
    inbetween_macros = ""

    for x in range(len(nodelist)):
        node = nodelist[x]

        if isinstance(node, LatexEnvironmentNode):
            if node.environmentname == 'frame':
                slides.append(
                    inbetween_macros +
                    tex_string[node.pos:node.pos+node.len]
                )
                slide_idx += 1
        elif isinstance(node, LatexGroupNode):
            if node.delimiters == ('{', '}'):
                node_tex = tex_string[node.pos:node.pos+node.len]
                if node_tex.find("frame") != -1:
                    slides.append(inbetween_macros + node_tex)
                    slide_idx += 1
                else:
                    slides[slide_idx] += node_tex
        elif isinstance(node, LatexMacroNode):
            node_tex = tex_string[node.pos:node.pos+node.len]

            if node.macroname in single_slide_macros:
                slides.append(node_tex)
                slide_idx += 1
            else:
                inbetween_macros += node_tex
                slides[slide_idx] += inbetween_macros
        else:
            slides[slide_idx] += tex_string[node.pos:node.pos+node.len]

    footer += "\\end{document}"

    return (header, footer, slides)


def compile_slide(arg):
    header, footer, slide, tex, pdf, nr = arg
    logger.info("Compiling slide %d %s" % (nr, tex))
    FNULL = open(os.devnull, 'w')
    try:
        with open(tex, "w") as out:
            out.write(header)
            if len(slide.strip()) == 0:
                error("Empty slide (%s)" % tex)
            out.write(slide)
            out.write(footer)
    except:
        error("Could not write %s" % tex)
    if os.path.isfile(pdf):
        os.remove(pdf)

    compile_command = [args.compiler, "--output-directory", args.prefix, "-halt-on-error", "-interaction=nonstopmode"] + args.compiler_option + [tex]
    logger.debug(compile_command)
    log = ""
    for i in range(int(args.runs)):
        p = Popen(compile_command, stdout=PIPE, stderr=STDOUT)
        out, err = p.communicate()
        if out:
            try:
                log += out.decode("utf-8")
            except:
                pass
    if not os.path.isfile(pdf):
        logger.warning("Could not compile slide %s" % tex)
        logger.warning(log)
        logger.warning("---- vvvvvvvvv ----")
        logger.warning(open(tex).read())
        try:
            with open(tex, "w") as out:
                out.write("")
        except:
            error("Could not write %s" % tex)


def merge_slides(hashes, slide_name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        merger = PdfFileMerger(strict=False)
        for i in hashes:
            slide_pdf = (slide_name % i).replace(".tex", ".pdf")
            try:
                merger.append(open(slide_pdf, "rb"))
            except:
                logger.warning("Could not add %s to the final output" % slide_pdf)

        try:
            with open(args.out, "wb") as pdf:
                merger.write(pdf)
        except Exception as e:
            error("Could not write %s" % args.out, e)


def create_slides(texfile):
    slide_name = "%s/%%s.tex" % args.prefix

    retries = 3
    while retries != 0:
        try:
            retries -= 1
            tex = open(texfile).read().split("\n")
        except:
            if retries == 0:
                fatal("Could not open '%s'" % texfile)
            else:
                time.sleep(0.3)

    header, footer, slides = parse_slides(tex)

    slide_hashes = [slide_hash(header + slides[i] + footer) for i in range(len(slides))]

    saved_hashes = os.listdir(args.prefix)

    for h in saved_hashes:
        try:
            hash_only = h.split(".")[0]
            if hash_only not in slide_hashes:
                os.remove(os.path.join(args.prefix, h))
        except:
            pass


    slide_changed = [(args.force or has_changed(header + slides[i] + footer, slide_name % slide_hashes[i], (slide_name % slide_hashes[i]).replace(".tex", ".pdf"))) for i in range(len(slides))]

    up_to_date = True

    recompile = []
    cnt = 0
    for i, slide in enumerate(slides):
        if slide_changed[i]:
            up_to_date = False
            cnt += 1
            recompile.append((header, footer, slide, slide_name % slide_hashes[i], (slide_name % slide_hashes[i]).replace(".tex", ".pdf"), cnt))

    logger.info("Compiling %d slides on %d cores (change with --smp <cores>)" % (len(recompile), args.smp))
    with multiprocessing.Pool(args.smp) as p:
        p.map(compile_slide, tuple(recompile))

    if up_to_date:
        logger.info("Everything is up to date, no recompilation required")
    else:
        merge_slides(slide_hashes, slide_name)


class SlideWatch(FileSystemEventHandler):
    def on_any_event(self, event):
        dst = ""
        try:
            dst = event.dest_path
        except:
            pass

        if dst.endswith(args.slides) or event.src_path.endswith(args.slides):
            create_slides(args.slides)
            logger.info("Ready")


def main():
    global args

    init_logging()
    parser = init_parser()
    args = parser.parse_args()

    texfile = args.slides

    if not os.path.exists(args.prefix):
        os.makedirs(args.prefix)

    create_slides(args.slides)

    if args.watch:
        event_handler = SlideWatch()
        observer = Observer()
        observer.schedule(event_handler, path=os.path.dirname(os.path.realpath(args.slides)), recursive=False)
        observer.start()

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()



if __name__ == "__main__":
    try:
        main()
    except AbortException:
        sys.exit(1)
