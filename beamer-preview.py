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
import base64
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from subprocess import Popen, STDOUT, PIPE
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
    parser.add_argument("--out", "-o", nargs="?", default="preview.pdf")
    parser.add_argument("--compiler", "-c", nargs="?", default="pdflatex")
    parser.add_argument("--ignore-errors", "-i", action="store_true", dest="ignore_errors", default=False)
    parser.add_argument("--prefix", "-p", nargs="?", default="beamer.out")
    parser.add_argument("--force", action="store_true", default=False)
    parser.add_argument("--watch", action="store_true", default=False)
    parser.add_argument("--smp", nargs="?", type=int, default=multiprocessing.cpu_count())
    parser.add_argument("--compiler-option", nargs="*", default=[], dest="compiler_option")
    parser.add_argument("--runs", "-r", nargs='?', default=1)
    parser.add_argument("--frames", "-f", action="store_true", default=False)
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


def single_slide_macro(tex):
    single_slide_macros = [
        'section',
        'maketitle',
        'imageslide',
        'fullFrameMovie',
        'fullFrameImage',
        'fullFrameImageZoomed'
    ]
    for s in single_slide_macros:
        if re.match(r"\s*\\%s{" % s, tex):
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


def parse_slides(tex):

    header = ""
    footer = ""
    slide = ""
    slides = []
    in_header = True
    in_slide = False
    slide_wrapper = False

    for i in range(len(tex)):
        if re.match(r"\s*\\begin\s*\{\s*document\s*\}", tex[i]):
            in_header = False
            if not in_slide:
                header += tex[i] + "\n"
            continue

        if re.match(r"\s*\{\s*", tex[i]):
            if in_slide:
                slide += tex[i] + "\n"
                continue

            slide_wrapper = True
            slide = tex[i] + "\n"
            continue
        if re.match(r"\s*\}\s*", tex[i]):
            if in_slide:
                slide += tex[i] + "\n"
                continue

            if slide_wrapper:
                slide += tex[i] + "\n"
                slides.append(slide)
                slide = ""
                footer = ""
                slide_wrapper = False
            else:
                error("Unexpected closing brackets at line %d" % (i + 1))

        elif re.match(r"\s*\\begin\s*\{\s*frame\s*\}", tex[i]):
            in_header = False
            if not in_slide:
                slide += tex[i] + "\n"
                in_slide = True
                continue
            else:
                error("frame inside frame at line %d" % (i + 1))
        elif re.match(r"\s*\\section\s*{\s*\w*", tex[i]):
            in_header = False
            if not in_slide:
                slide = ""
                if len(tex[i].strip()) == 0:
                    error("frame without content at line %d" % (i + 1))
                slides.append(tex[i] + "\n")
                continue
            else:
                error("section inside frame at line %d" % (i + 1))
        elif re.match(r"\s*\\maketitle", tex[i]):
            in_header = False
            if not in_slide:
                slide = ""
                if len(tex[i].strip()) == 0:
                    error("frame without content at line %d" % (i + 1))
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
                if len(slide.strip()) == 0:
                    error("frame without content at line %d" % (i + 1))
                if not slide_wrapper:
                    slides.append(slide)
                    slide = ""
                    footer = ""
                    continue
        elif single_slide_macro(tex[i]):
            if not in_slide:
                slide = ""
                slides.append(tex[i] + "\n")
                continue

        else:
            if in_slide or slide_wrapper:
                slide += tex[i] + "\n"
                continue

        if not in_slide and not slide_wrapper and in_header:
            header += tex[i] + "\n"
            continue
        elif not in_slide and not slide_wrapper and not in_header:
            footer += tex[i] + "\n"
            continue

    if in_slide:
        error("Missing frame end")
    if in_header:
        logger.warning("No slides found")

    return (header, footer, slides)


def build_slide(header, slide, footer, nr = 1):
    s = header
    if args.frames: s += "\\addtocounter{framenumber}{%d}" % nr
    s += slide
    s += footer
    return s


def compile_slide(arg):
    header, footer, slide, tex, pdf, nr, idx = arg
    logger.info("Compiling slide %d %s" % (nr, tex))
    FNULL = open(os.devnull, 'w')
    try:
        with open(tex, "w") as out:
            if len(slide.strip()) == 0:
                error("Empty slide (%s)" % tex)
            out.write(build_slide(header, slide, footer, idx))
    except:
        error("Could not write %s" % tex)
    if os.path.isfile(pdf):
        os.remove(pdf)

    compile_command = [args.compiler, "--output-directory", args.prefix, "-halt-on-error"] + args.compiler_option + [tex]
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
        with open(pdf, "wb") as out:
            out.write(base64.b64decode("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Jjvkyvp1fX8rR/QeOaEBiZUVox+6hrhxeUAl8VcnRHCuUQotN0l5hl804lwM1ygSFpaxss4bRvgsO6oSIcUgcZEn4Vixj2O8bHojib+a8U8g/EgItUng733dSFhyFG1FuZxFad2WcsQA8wMDaPW12jf0SibG4JBzfHoruyq4yBtdQX9XLN8aMyDrLRm0zxNSI6lWeD704iEL28Q5abqKlANGk5P+9AvnBSAf/B1r0vMA3I0EghYmpEC2lvFLEdg3TjpgBmsWHM7rhdQsbpX4+5WOozYRKeMEh7cl/1ywOVhJ6rwMyvuAyisLwaVTxF4cYLW+hjn0lXIQuCgKmqjrfUm3qbQ1wxo/6QvzQJqd8doXrg3sSQxxKewTLuSvZTzPpWmGj6WVqUtRAHeTYLIZxy/5GPqsisjacImf0/ADORZeiPal2fjJ4ELJ1xOZvyqw9y8f7ykWoCLY6y7hryV8X9bP9d/X/wMSTGzqCmVuZHN0cmVhbQplbmRvYmoKNSAwIG9iagogICA1Njg5CmVuZG9iagozIDAgb2JqCjw8CiAgIC9FeHRHU3RhdGUgPDwKICAgICAgL2EwIDw8IC9DQSAxIC9jYSAxID4+CiAgID4+Cj4+CmVuZG9iagoyIDAgb2JqCjw8IC9UeXBlIC9QYWdlICUgMQogICAvUGFyZW50IDEgMCBSCiAgIC9NZWRpYUJveCBbIDAgMCAxNDQwIDgxMCBdCiAgIC9Db250ZW50cyA0IDAgUgogICAvR3JvdXAgPDwKICAgICAgL1R5cGUgL0dyb3VwCiAgICAgIC9TIC9UcmFuc3BhcmVuY3kKICAgICAgL0kgdHJ1ZQogICAgICAvQ1MgL0RldmljZVJHQgogICA+PgogICAvUmVzb3VyY2VzIDMgMCBSCj4+CmVuZG9iagoxIDAgb2JqCjw8IC9UeXBlIC9QYWdlcwogICAvS2lkcyBbIDIgMCBSIF0KICAgL0NvdW50IDEKPj4KZW5kb2JqCjYgMCBvYmoKPDwgL1Byb2R1Y2VyIChjYWlybyAxLjE2LjAgKGh0dHBzOi8vY2Fpcm9ncmFwaGljcy5vcmcpKQogICAvQ3JlYXRpb25EYXRlIChEOjIwMjEwNTE1MTg0MDM3KzAyJzAwKQo+PgplbmRvYmoKNyAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZwogICAvUGFnZXMgMSAwIFIKPj4KZW5kb2JqCnhyZWYKMCA4CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwNjA5NSAwMDAwMCBuIAowMDAwMDA1ODc2IDAwMDAwIG4gCjAwMDAwMDU4MDQgMDAwMDAgbiAKMDAwMDAwMDAxNSAwMDAwMCBuIAowMDAwMDA1NzgxIDAwMDAwIG4gCjAwMDAwMDYxNjAgMDAwMDAgbiAKMDAwMDAwNjI3NiAwMDAwMCBuIAp0cmFpbGVyCjw8IC9TaXplIDgKICAgL1Jvb3QgNyAwIFIKICAgL0luZm8gNiAwIFIKPj4Kc3RhcnR4cmVmCjYzMjgKJSVFT0YK"))


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

    try:
        tex = open(texfile).read().split("\n")
    except:
        fatal("Could not open '%s'" % texfile)

    header, footer, slides = parse_slides(tex)

    slide_hashes = [slide_hash(build_slide(header, slides[i], footer, i + 1)) for i in range(len(slides))]

    saved_hashes = os.listdir(args.prefix)

    for h in saved_hashes:
        try:
            hash_only = h.split(".")[0]
            if hash_only not in slide_hashes:
                os.remove(os.path.join(args.prefix, h))
        except:
            pass


    slide_changed = [(args.force or has_changed(build_slide(header, slides[i], footer, i + 1), slide_name % slide_hashes[i], (slide_name % slide_hashes[i]).replace(".tex", ".pdf"))) for i in range(len(slides))]

    up_to_date = True

    recompile = []
    cnt = 0
    for i, slide in enumerate(slides):
        if slide_changed[i]:
            up_to_date = False
            cnt += 1
            recompile.append((header, footer, slide, slide_name % slide_hashes[i], (slide_name % slide_hashes[i]).replace(".tex", ".pdf"), cnt, i + 1))

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
