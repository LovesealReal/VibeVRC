# VibeVRC

Control your partner's Lovense from your VRChat avatar menu, from anywhere.

You both run VibeVRC. Yours reads your avatar menu over OSC and sends it through a public relay to theirs, which runs their toy through Intiface. It only works if the toy owner turns on sharing and sends you their code.

- Toy owners can share their code with only those they want
- The toy owner can pause, cap the strength, or make a new code whenever they want
- Works alongside VRCFT, OSCGoesBrrr and other OSC apps

## Building

You only need this if you're building it yourself. To use VibeVRC, just download VibeVRC.exe from Releases.

Needs Python 3.11. If Visual Studio Build Tools are installed, the build also compiles PyInstaller's launcher from source, which cuts down on antivirus false alarms. Without them it still builds fine.

Run build.bat and you get VibeVRC.exe in dist. Intiface's engine is built in, so the toy owner doesn't need to install anything.

To run from source: `pip install -r requirements.txt` then `python main.py`.

The engine in intiface/ is intiface-engine 5.0.2 from https://github.com/buttplugio/buttplug.
