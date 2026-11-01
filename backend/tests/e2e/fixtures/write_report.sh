#!/bin/sh
# Template of the sandbox command in the Phase 4 end-to-end test.
# The fake model puts the shell quoted report text into the placeholder on the
# last line and runs this script through `sh -c`, so the container writes the
# workspace file.
set -eu
printf '%s' {report} > /workspace/report.md
