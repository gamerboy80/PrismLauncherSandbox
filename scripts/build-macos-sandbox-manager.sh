#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'The sandbox manager requires macOS.' >&2
  exit 1
fi
prism_build_dir="${PRISM_SANDBOX_BUILD_DIR:-$PWD/build-macos-sandbox-manager}"
prism_install_dir="${PRISM_SANDBOX_INSTALL_DIR:-$PWD/install-macos-sandbox-manager}"
prism_brew_prefix="$(brew --prefix)"
prism_java_home="${JAVA_HOME:-$(/usr/libexec/java_home -v 21)}"
if [[ ! -f libraries/libnbtplusplus/CMakeLists.txt ]]; then
  git submodule update --init libraries/libnbtplusplus
fi
export PKG_CONFIG_PATH="$prism_brew_prefix/opt/libarchive/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
cmake -S . -B "$prism_build_dir" -G Ninja \
  -DCMAKE_BUILD_TYPE="${PRISM_SANDBOX_BUILD_TYPE:-RelWithDebInfo}" -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_INSTALL_PREFIX="$prism_install_dir" -DCMAKE_PREFIX_PATH="$prism_brew_prefix" \
  -DLauncher_MACOS_SANDBOX_MANAGER=ON -DLauncher_USE_PCH=OFF \
  -DJava_JAVA_EXECUTABLE="$prism_java_home/bin/java" \
  -DJava_JAVAC_EXECUTABLE="$prism_java_home/bin/javac" \
  -DJava_JAR_EXECUTABLE="$prism_java_home/bin/jar"
cmake --build "$prism_build_dir" --parallel "${PRISM_SANDBOX_JOBS:-8}"
cmake --install "$prism_build_dir"
if [[ "$("$prism_brew_prefix/opt/qtbase/bin/qtpaths" --qt-version)" == 6.11.2 ]]; then
  /usr/bin/python3 -I -S scripts/macos-sandbox/fix_qt_cocoa.py \
    --build-dir "$PWD/build-macos-sandbox-qt-fix" --qt-prefix "$prism_brew_prefix/opt/qtbase"
  /usr/bin/python3 -I -S scripts/macos-sandbox/package.py "$prism_install_dir/PrismLauncher.app" \
    --cocoa-plugin "$PWD/build-macos-sandbox-qt-fix/plugin-build/libqcocoa.dylib"
else
  /usr/bin/python3 -I -S scripts/macos-sandbox/package.py "$prism_install_dir/PrismLauncher.app"
fi
