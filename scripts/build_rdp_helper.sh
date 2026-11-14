#!/usr/bin/env bash
# Compile the embedded RDP helper and audio device plugin against FreeRDP.
set -euo pipefail
cd "$(dirname "$0")/../native"

FRDP="$(brew --prefix freerdp)"

cc rdp_helper.c -o rdp_helper \
    $(pkg-config --cflags --libs freerdp-client3 freerdp3 winpr3)

cc -dynamiclib -o librdpsnd-client-pipe.dylib rdpsnd_pipe.c \
    -I"$FRDP/include/freerdp3" -I"$FRDP/include/winpr3"

# Also install the audio plugin into FreeRDP's addin search path so the
# source-tree run (./run.sh) finds it even without the custom loader.
cp librdpsnd-client-pipe.dylib "$FRDP/lib/freerdp3/" 2>/dev/null || true

echo "built native/rdp_helper and native/librdpsnd-client-pipe.dylib"
