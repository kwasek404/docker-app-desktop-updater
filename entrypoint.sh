#!/bin/bash
set -ex

uid=$1
re='^[0-9]+$'

if [ -z $uid ]; then
  uid=1000
elif [[ ! $uid =~ $re ]]; then
  echo "error: Invalid user uid: $uid"
  exit 1
fi

useradd -u $uid -m -s /bin/bash docker

sudo -i -u docker << EOF
  set -ex
  
  cp -rf /.ssh ~/.ssh

  git config --global user.email "travis@travis-ci.org"
  git config --global user.name "Travis CI"

  ssh-keyscan github.com >> ~/.ssh/known_hosts

  git clone $2

  (cd docker-app-desktop && git checkout $3)

  python /usr/src/updater/updater.py --dir docker-app-desktop

  (cd docker-app-desktop && git push && git push --tags)
EOF
