# test-niobot
A simple test bot I'm running and developing [nio-botlib](https://github.com/EEKIM10/nio-botlib) on.

As this is basically a live dev tool, any sort of interoperability is not guaranteed. It works on my machine.
Furthermore, it may not work at all due to broken new features.

Also be aware of the security risks, etc. etc.

__Do not use this in public rooms etc., there is very little safeguarding against some rando going `?echo @room` etc.__

## Usage
1. clone
2. Put your token in a config.py file, or modify main.py's last line or whatever suits you
3. pip3 install -r requirements.txt
4. python3 main.py

### Developing
If you want to develop, you should use `pipenv`. Clone the repository, then run `pipenv install --dev`.
You can then access the virtual environment through `pipenv shell`. Your IDE will likely auto-detect the pipenv
virtual environment for you.
