# LaTeX Beamer Preview

This helper script recompiles only modified slides for LaTeX Beamer presentations. The script detects automatically (using a file-system watch) whether the slides have changed and starts the recompilation.
Compiling all slides is furthermore optimized by running the compilation processes in parallel.

# Usage

To always recompile changed slides, simply run
```
python3 beamer-preview.py --watch <slides.tex>
```

This recompiles the modified (or added) slides everytime the file is saved. The generated preview file is in the same folder and called `preview.pdf`.

The tool supports several command-line parameters:

| Parameter | Description |
|-|-|
--out [filename] / -o [filename]  | File name of preview PDF (default: preview.pdf)
--compiler [compiler] / -c [compiler] | LaTeX compiler to use. Supports pdflatex, xelatex, lualatex (default: pdflatex)
--ignore-errors | Try to continue building the PDF even if there are errors (default: false)
--prefix [folder] / -p [folder] | Folder in which auxiliary files are stored (default: beamer.out)
--force | Force recompilation even if no change was detected (default: false)
--watch | Monitor the file system to detect changes in the LaTeX file (default: false)
--smp [cores] | Number of CPU cores to use for multithreaded compilation (default: number of currently available CPU cores)
--compiler-option [option] | Option passed to the compiler (can be provided multiple times)
--runs [count] | Number of compilation runs per slide (default: 1)

# Features

# Limitations / Known Issues
