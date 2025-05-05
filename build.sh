# build.sh
#!/bin/bash
set -o errexit

# Install system dependencies
apt-get update
apt-get install -y chromium chromium-driver
