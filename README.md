# test-niobot
A simple test bot I'm running and developing [niobot](https://github.com/EEKIM10/niobot) on.

As this is basically a live dev tool, any sort of interoperability is not guaranteed. It works on my machine.
Furthermore, it may not work at all due to broken new features.

Also be aware of the security risks, etc. etc.

[You can use my public instance here (running the latest master commits of both niobot and this repo)](https://matrix.to/#/@jimmy-bot:nexy7574.co.uk). The prefix is `?`.
It may additionally be `!` if I'm testing something on a different machine.

__Do not use this in public rooms etc., there is very little safeguarding against some rando going `?echo @room` etc.__

## Usage

1. clone
2. Put your token in a config.py file, or modify main.py's last line or whatever suits you
3. pip3 install -r requirements.txt
4. python3 main.py
