from distutils.core import setup
import py2exe

includes = ['matplotlib', 'numpy', 'Tkinter', 'tcl', 'Tkconstants', 'pyaudio', 'google', 'six' ]
excludes = ['_gtkagg', '_tkagg', 'curses', 'pywin.debugger', 'pywin.debugger.dbgcon', 'pywin.dialogs' ]
packages = []
dll_excludes = []

setup(
    options = {"py2exe": {"compressed": 0,
                          "optimize": 0,
                          "includes": includes,
                          "excludes": excludes,
                          "packages": packages,
                          "dll_excludes": dll_excludes,
                          "bundle_files": 1,
                          "dist_dir": "./py2app/dist/",
                          "xref": False,
                          "skip_archive": False,
                          "ascii": False,
                          "custom_boot_script": '',
                         }
              },
    console="main.py",
)