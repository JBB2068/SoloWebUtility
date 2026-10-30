Solo Web Utility v1.2.3 -- Mac installer
=========================================

Installs Solo Web Utility and its firmware on your 3DR Solo over SoloLink Wi-Fi,
using the ssh that comes with macOS. Nothing is installed on the Mac. Afterwards,
use Solo Web Utility in a browser at http://10.1.1.10 and delete this folder.

1. Turn on the Solo and the Controller.
2. Join the SoloLink_... Wi-Fi on the Mac. It has no internet; that is normal.
3. Double-click "Install Solo Web Utility.command". A Terminal window shows the
   progress. Keep it open until it says Done (about 15-25 minutes).

The first time, macOS may say the file "cannot be opened" or is "from an
unidentified developer", because it is not signed by Apple:
  - macOS 15 (Sequoia) and later: click Done, then open System Settings >
    Privacy & Security, scroll down and click "Open Anyway" next to
    "Install Solo Web Utility.command".
  - macOS 14 and earlier: right-click (or Control-click) the file, choose Open,
    then click Open.
  - Or open Terminal, type   bash   followed by a space, drag the .command file
    into the Terminal window and press Return.

If macOS asks whether Terminal may find devices on your local network, click Allow.
If Terminal asks for a password (older macOS only), type: TjSDBkAu

Running it again is safe: it reinstalls the app files and skips firmware that is
already on the Solo.
