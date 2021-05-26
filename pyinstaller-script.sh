conda create --prefix ./stt-envs python=3.7

pyinstaller cli.py --name stt-app-dev --additional-hooks-dir
