#!/usr/bin/env bash
set -eu
cp -R "$(dirname "$0")/files/." .
chmod +x bin/phoronix-test-suite
./bin/phoronix-test-suite batch-install pts/fluxsim > /dev/null
