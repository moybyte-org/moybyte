# CI's host job (.github/workflows/ci.yml, `runs-on: ubuntu-latest`) as an
# image: what tools/ci_image/run.sh runs preflight's host suite in.
#
# `ubuntu-latest` is whatever GitHub maps it to, which is not what its README
# table says: every job since 2026-11-04 logs "Image: ubuntu-26.04". The
# software is that runner image's, version for version where the suite reaches
# it (actions/runner-images, images/ubuntu/Ubuntu2604-Readme.md): the
# archive's gcc 15, clang, make, file and OpenSSH client; git from the git-core
# PPA; Node.js 24 and CMake 4.4 from their release archives. Plus what ci.yml
# installs itself: gcc-multilib, and Python 3.11 -- the very build
# actions/setup-python unpacks on that runner (actions/python-versions), into
# the toolcache path its RUNPATH names. 3.11 is the half of CI's matrix the
# local 3.10 venv does not cover.
#
# ubuntu:26.04 as published 2026-10-29.
FROM ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c

ARG HOST_UID=1000
ARG HOST_GID=1000
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    SDL_VIDEODRIVER=dummy \
    SDL_AUDIODRIVER=dummy

RUN apt-get update -y \
 && apt-get install -y --no-install-recommends software-properties-common gpg-agent \
 && add-apt-repository -y ppa:git-core/ppa \
 && apt-get update -y \
 && apt-get install -y --no-install-recommends \
      build-essential gcc-multilib clang make pkg-config patch \
      git file openssh-client ca-certificates curl xz-utils zstd \
 && rm -rf /var/lib/apt/lists/*

ARG PYTHON=3.11.18
ARG PYTHON_URL=https://github.com/actions/python-versions/releases/download/3.11.18-40260280589/python-3.11.18-linux-26.04-x64.tar.gz
ARG PYTHON_SHA256=0a446ea4a20256d83f10c38f05707e6028a8ecd462abcf2e89ee48fc0c3e32e7
ARG NODE=v24.22.2
ARG NODE_SHA256=48cc0e21e048790e54c0cf5749c2725005313d652651c40b7a21feb9697aaf75
ARG CMAKE=4.4.5
ARG CMAKE_SHA256=ed85135c5a5135b45e50423cb406dea04ca3338da0b8ef3c9ede963559680be0
RUN mkdir /tmp/py && cd /tmp/py \
 && curl -fsSLo py.tgz "${PYTHON_URL}" \
 && echo "${PYTHON_SHA256}  py.tgz" | sha256sum -c - \
 && tar -xzf py.tgz && rm py.tgz \
 && AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache bash ./setup.sh >/dev/null \
 && ln -s /opt/hostedtoolcache/Python/${PYTHON}/x64/bin/python3.11 /usr/local/bin/python3.11 \
 && cd /tmp && rm -rf /tmp/py \
 && curl -fsSLo node.tar.xz https://nodejs.org/dist/${NODE}/node-${NODE}-linux-x64.tar.xz \
 && echo "${NODE_SHA256}  node.tar.xz" | sha256sum -c - \
 && tar -xJf node.tar.xz -C /usr/local --strip-components=1 --exclude='*.md' --exclude=LICENSE \
 && curl -fsSLo cmake.tar.gz https://github.com/Kitware/CMake/releases/download/v${CMAKE}/cmake-${CMAKE}-linux-x86_64.tar.gz \
 && echo "${CMAKE_SHA256}  cmake.tar.gz" | sha256sum -c - \
 && tar -xzf cmake.tar.gz -C /usr/local --strip-components=1 \
 && rm -f node.tar.xz cmake.tar.gz

# The host's UID and GID, so what the suite writes into the mounted tree stays
# the owner's; and a passwd entry for it, which OpenSSH's client requires.
RUN (userdel -r ubuntu 2>/dev/null || true) \
 && groupadd -o -g "${HOST_GID}" ci \
 && useradd -o -m -u "${HOST_UID}" -g "${HOST_GID}" -s /bin/bash ci \
 && mkdir -p /ci/pip /home/ci/.cache \
 && chown -R "${HOST_UID}:${HOST_GID}" /ci /home/ci
ENV HOME=/home/ci \
    PIP_CACHE_DIR=/ci/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1
USER ci
