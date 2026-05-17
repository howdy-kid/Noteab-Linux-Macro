# Noteab Linux Macro

This just the Coteab/Noteab (Idek atp what its called) macro but ported to linux.
I just added all the features and stuff from the Noteab-Macro to Root1527's Noteab Macro Linux. Some of the stuff Im still working on so don't expect everything (mainly the paths, my laptop that runs linux is super slow so I can't really debug things properly). ALSO THE F1/F2 BUTTONS DON'T WORK RN YOU NEED TO USE ROOT BUT THE GUI DOESN'T WORK WITH ROOT. Also note that unfortunately you need to either import a config file that contains the calibrations or set the calibrations yourself.

# Installation
 
First update your package manager (like apt)
```bash
sudo apt update
```
and download git and python if you haven't done so already.
```bash
sudo apt install git python
```
Next, download this repository either by pulling it:
```bash
git pull https://github.com/howdy-kid/Noteab-Linux-Macro.git
```
or by using the network downloader:
```bash
wget https://github.com/howdy-kid/Noteab-Linux-Macro/archive/refs/heads/main.zip
unzip main.zip
mv main.zip Noteab-Linux-Macro
```
After that, enter the macro's directory.
```bash
cd
```
You need a python virtual environment, so run the following commands:
```bash
sudo apt install python3-venv
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
After you made your virtual environment and downloaded all the requirements, simply run the macro
```bash
./venv/bin/python biome_activity_source.py
```

