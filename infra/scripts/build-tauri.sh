#!/usr/bin/env bash
# build-tauri.sh — cross-platform build helper.
set -euo pipefail
cd "$(dirname "$0")/../.."

case "$(uname -s)" in
  Darwin)   BUNDLES="--bundles app,dmg" ;;
  Linux)    BUNDLES="--bundles deb,appimage" ;;
  MINGW*|CYGWIN*|MSYS*) BUNDLES="--bundles nsis,msi" ;;
  *)        BUNDLES="--bundles app" ;;
esac

cd apps/desktop
pnpm install
pnpm tauri build $BUNDLES