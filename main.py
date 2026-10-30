"""Application entry point for Python and the console-free Windows executable."""
import ctypes
import sys
import traceback

from paths import data_dir


def main():
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Paperport.Desktop.v1_1_2')
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'paperport.log').open('a', encoding='utf-8', buffering=1) as log:
        sys.stdout = sys.stderr = log
        try:
            if '--self-test' in sys.argv:
                from self_test import run
                run()
                print('PAPERPORT_SELF_TEST_PASSED', flush=True)
            else:
                import customtkinter as ctk
                from app import App
                ctk.set_appearance_mode('dark')
                App().mainloop()
        except Exception:
            traceback.print_exc(file=log)
            if '--self-test' in sys.argv:
                raise SystemExit(1)
            ctypes.windll.user32.MessageBoxW(None, f'Paperport could not start. Details are in:\n{folder / "paperport.log"}', 'Paperport', 0x10)
            raise SystemExit(1)


if __name__ == '__main__':
    main()
