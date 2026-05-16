# Coteab Macro notice.
## I. Introduction
1) New developer??!?
- Hi everyone. I am Vapure, aka "criticize." or "C". I work with Akito and a few others to maintain this macro. If you have any inquires, feel free to contact us on discord, or by email `work.vapure@gmail.com`.
- You might have seen me somewhere inside other Sol's RNG related servers.
2) The project.
- This project is a fork of the original [Noteab's Biome Macro](https://github.com/NotWindyZ/Noteab-Macro/).
- Abides by the [Apache 2.0 license](https://github.com/NotWindyZ/Noteab-Macro/blob/main/LICENSE).
## II. About Coteab Macro
1) What does Coteab Macro offer?
- Merchant detection using OCR detection
- Auto purchase from merchants.
- Biome detections, 99.99% accurate most of the time using logs reading method.
- Aura detections, again, 99.99% accurate most of the time using logs reading method.
- Webhooks for notification to your Discord server!!
- Auto popping potions inside of any biome (Including GLITCHED, DREAMSPACE, CYBERSPACE)
- Allowing for mouse clicks to prevent disconnection from the game. Very suitable for afk sessions packed with features.
- Multi webhook support.
- Macro session time report.
## III. Linux quick start
1) Install the Linux system packages used by the macro automation stack:
```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-tk python3-dev scrot tesseract-ocr xclip libxcb-xinerama0
```

2) Create a virtual environment and install the Python dependencies:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Launch the cross-platform bootstrapper:
```bash
python biome_activity_source.py
```

The launcher now detects Linux and downloads a native Linux release asset when one exists, such as an AppImage, tarball, zip, or executable named `CoteabMacro`. You can override the asset name with `COTEAB_ASSET_NAME` if a release uses a custom filename:
```bash
COTEAB_ASSET_NAME=CoteabMacro-linux-x86_64.AppImage python biome_activity_source.py
```

Linux notes:
- The macro uses screen capture, keyboard, and mouse automation. X11 sessions are the most compatible option; Wayland sessions may block global input or screenshots depending on your compositor settings.
- Some keyboard/mouse backends may require elevated permissions or udev configuration on Linux.
- The existing calibration and route files are coordinate-based, so you may need to recalibrate them for your Linux display scale, window manager decorations, and Roblox window size.

## IV. FAQs and common fixes
- Q: Macro has virus?<br>
- A: On Windows, this can be caused by false positives. You can check out the source code or reverse engineer if you wish. Or put the file into a virtual machine/Virus Total. If you couldn't run your macro file due to false positives, try disabling your anti-virus, or refer to these tutorials: https://youtu.be/zGiNGnX5dYg?si=uC-qDKqVoC68txy8 https://youtu.be/_0_A9D0JeVo?si=ahXKpVYbs-HhOwSC<br>

- Q: Macro using my rare potions?<br>
- A: Either set a mouse delay to 1000-2000 milliseconds, or calibrate OCR-failsafe. <br>

- Q: How to calibrate everything?<br>
- A: https://youtu.be/s2S7Bncx9ns?si=8VBg8AaE1-9roLNK<br>

- Q: What is the first item slot?<br>
- A: https://i.postimg.cc/9X6tt3Wg/image.png<br>

- Q: The macro wouldn't do anything it wouldn't start??<br>
- A: There are two fixes: make sure the macro is inside a folder with your config file (and extracted). If that does not work on Windows, run the macro as administrator; on Linux, check that your input/screenshot permissions are configured.<br>

- Q: The macro opens but it's only black / white??<br>
- A: On Windows, re-install / install WebView2: https://developer.microsoft.com/en-us/microsoft-edge/webview2/?form=MA13LH. On Linux, prefer an X11 session and verify that your AppImage or native binary has executable permissions.<br>

- Q: The macro searches the item up then closes out instantly??<br>
- A: One of two things you can do -- Recalibrate OCR-failsafe, or turn off the option completely. If you still want a "failsafe", set your input delay to 1000-2000 milliseconds.

- Q: I set up OCR-failsafe correctly but it misdetects items anyway??<br>
- A: Use a custom font, preferably Arial or Rubik.<br>

- Q: Macro doesn't press "e" around merchants??<br>
- A: Make sure you have something set inside of your millsecond input delay. If you don't have a problem with the macro clicking too fast, just set it to 0.<br>

- Q: Macro spams reel??<br>
- A: Recalibrate midbar color sample.<br>

- Q: Macro spams fish button??<br>
- A: Recalibrate fishing pixel detection.<br>

- Anything not listed above -- do a full re-installation of the macro. If that still doesn't work, make a suport forum in the Discord server.
