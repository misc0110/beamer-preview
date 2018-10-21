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
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from subprocess import call, STDOUT
from PyPDF2 import PdfFileMerger

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
    parser.add_argument("--out", "-o", nargs="?", default="slide.pdf")
    parser.add_argument("--compiler", "-c", nargs="?", default="latexrun")
    parser.add_argument("--ignore-errors", "-i", action="store_true", dest="ignore_errors", default=False)
    parser.add_argument("--prefix", "-p", nargs="?", default="_slide_")
    parser.add_argument("--force", action="store_true", default=False)
    parser.add_argument("--watch", action="store_true", default=False)
    parser.add_argument("--smp", nargs="?", default=multiprocessing.cpu_count())
    parser.add_argument("slides")

    return parser


def has_changed(content, fname):
    try:
        return content != open(fname).read()
    except:
        return True


class AbortException(Exception):
    pass


def fatal(msg):
    logger.critical(msg)
    raise AbortException()


def error(msg):
    logger.error(msg)
    if not args.ignore_errors:
        raise AbortException()


def parse_slides(tex):

    header = ""
    footer = ""
    slide = ""
    slides = []
    in_header = True
    in_slide = False

    for i in range(len(tex)):
        if re.match(r"\s*\\begin\s*\{\s*frame\s*\}", tex[i]):
            in_header = False
            if not in_slide:
                slide = tex[i] + "\n"
                in_slide = True
                continue
            else:
                error("frame inside frame at line %d" % (i + 1))
        elif re.match(r"\s*\\section\s*{\s*\w*", tex[i]):
            in_header = False
            if not in_slide:
                slide = ""
                slides.append(tex[i] + "\n")
                continue
            else:
                error("section inside frame at line %d" % (i + 1))
        elif re.match(r"\s*\\maketitle", tex[i]):
            in_header = False
            if not in_slide:
                slide = ""
                slides.append(tex[i] + "\n")
                continue
            else:
                error("maketitle inside frame at line %d" % (i + 1))
        elif re.match(r"\s*\\end\s*{\s*frame\s*}", tex[i]):
            if not in_slide:
                error("frame end without frame begin at line %d" % (i + 1))
            else:
                in_slide = False
                slide += tex[i] + "\n"
                slides.append(slide)
                slide = ""
                footer = ""
                continue

        else:
            if in_slide:
                slide += tex[i] + "\n"
                continue

        if not in_slide and in_header:
            header += tex[i] + "\n"
            continue
        elif not in_slide and not in_header:
            footer += tex[i] + "\n"
            continue

    if in_slide:
        error("Missing frame end")
    if in_header:
        logger.warning("No slides found")

    return (header, footer, slides)


def compile_slide(arg):
    header, footer, slide, tex, pdf = arg
    logger.info("Compiling slide %s" % (tex))
    FNULL = open(os.devnull, 'w')
    try:
        with open(tex, "w") as out:
            out.write(header)
            out.write(slide)
            out.write(footer)
    except:
        error("Could not write %s" % tex)
    if os.path.isfile(pdf):
        os.remove(pdf)
    call([args.compiler, tex], stdout=FNULL, stderr=STDOUT)
    if not os.path.isfile(pdf):
        logger.warning("Could not compile slide %s" % tex)
        try:
            with open(tex, "w") as out:
                out.write("")
        except:
            error("Could not write %s" % tex)


def merge_slides(count, slide_name):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        merger = PdfFileMerger(strict=False)
        for i in range(count):
            slide_pdf = (slide_name % i).replace(".tex", ".pdf")
            try:
                merger.append(open(slide_pdf, "rb"))
            except:
                logger.warning("Could not add %s to the final output" % slide_pdf)

        try:
            with open(args.out, "wb") as pdf:
                merger.write(pdf)
        except:
            error("Could not write %s" % args.out)


def create_slides(texfile):
    slide_name = "%s%%d.tex" % args.prefix

    try:
        tex = open(texfile).read().split("\n")
    except:
        fatal("Could not open '%s'" % texfile)

    header, footer, slides = parse_slides(tex)

    slide_changed = [(args.force or has_changed(header + slides[i] + footer, slide_name % i)) for i in range(len(slides))]

    up_to_date = True

    recompile = []
    for i, slide in enumerate(slides):
        if slide_changed[i]:
            up_to_date = False
            recompile.append((header, footer, slide, slide_name % i, (slide_name % i).replace(".tex", ".pdf")))

    logger.info("Compiling on %d cores (change with --smp <cores>)" % args.smp)
    with multiprocessing.Pool(args.smp) as p:
        p.map(compile_slide, tuple(recompile))

    if up_to_date:
        logger.info("Everything is up to date, no recompilation required")

    merge_slides(len(slides), slide_name)


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
    else:
        create_slides(args.slides)



if __name__ == "__main__":
    try:
        main()
    except AbortException:
        sys.exit(1)
