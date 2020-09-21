#!/usr/bin/env python

import logging
import requests
import sys
import os
import yaml
import re
import datetime
import difflib
import zlib
import argparse
import git
from pydpkg import Dpkg

class main():
  mainDir = None
  images = None
  args = None
  repo = None

  def __init__(self):
    logging.basicConfig(format='%(levelname)s %(message)s', stream=sys.stdout)
    self.log = logging.getLogger(__name__)
    self.log.setLevel(logging.DEBUG)
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', type=str, required=True)
    self.args = parser.parse_args()
    self.mainDir = os.path.realpath(self.args.dir)
    with open('{}/images.yaml'.format(self.mainDir), 'r') as f:
      self.images = yaml.load(f, Loader=yaml.SafeLoader)
    self.repo = git.Repo(self.mainDir)

  def getFile(self, dir, filename):
    self.log.info('Reading file: {}/{}'.format(dir, filename))
    try:
      with open('{}/{}/{}'.format(self.mainDir, dir, filename), 'r') as f:
        return f.read()
    except FileNotFoundError:
      self.log.info('Missing file: {}/{}'.format(dir, filename))
      return ''
  
  def overwriteFile(self, dir, filename, content):
    self.log.info('Writing file: {}/{}'.format(dir, filename))
    self.checkAndCreateDir('{}/{}'.format(self.mainDir, dir))
    fileWithPath = '{}/{}/{}'.format(self.mainDir, dir, filename)
    with open(fileWithPath, 'w') as f:
      f.write(content)
    self.repo.index.add([fileWithPath])
  
  def getFromVariables(self, content):
    return re.findall(r'^FROM .*', content)
  
  def getFromDecode(self, fromValue):
    return fromValue.split()[1].split(':')
  
  def checkAndCreateDir(self, dir):
    if not os.path.exists(dir):
      self.log.info('Dir: {} doesnt exists, creating'.format(dir))
      os.makedirs(dir)
  
  def getMultilineDiff(self, a, b):
    return ''.join(difflib.ndiff(a.splitlines(1), b.splitlines(1)))
  
  def commitAndTag(self, tagName):
    self.repo.index.commit('CI: {}'.format(tagName))
    self.repo.create_tag(tagName)
    self.repo.git.reset('--hard')

  def checkAndUpdateVersionsRegistry(self, template, dockerfile):
    dir = template['name']
    yamlcontent = template['dockerfilecontent']
    currentContent = self.getFile(dir, dockerfile)
    fromValue = self.getFromVariables(yamlcontent)[0]
    fromImage, fromVersion = self.getFromDecode(fromValue)
    latestVersion = self.getRegistryLatest(fromImage, template['tagfilter'])
    yamlcontent = yamlcontent.replace(fromValue, 'FROM {}:{}'.format(fromImage, latestVersion))
    if yamlcontent != currentContent:
      self.log.info('Updating image')
      self.log.info("DIFF:\n{}".format(self.getMultilineDiff(currentContent, yamlcontent)))
      self.overwriteFile(dir, dockerfile, yamlcontent)
      version = self.checkAndUpdateVersionFile(dir, latestVersion)
      self.commitAndTag('{}_{}'.format(dir, version))
      return version
    return self.getFile(dir, 'version')
  
  def checkAndUpdateVersionFile(self, dir, version):
    versionFile = 'version'
    content = self.getFile(dir, versionFile)
    if content == '' or version != '.'.join(content.split('.')[:-1]):
      versionWithBuild = '{}.01'.format(version)
    else:
      versionWithBuild = '{}.{:02d}'.format(version, int(content.split('.')[-1])+1)
    if content != versionWithBuild:
      self.log.info('Updating version')
      self.log.info("DIFF:\n{}".format(self.getMultilineDiff(content, versionWithBuild)))
      self.overwriteFile(dir, versionFile, versionWithBuild)
    return versionWithBuild


  def getRegistryLatest(self, fromImage, tagfilter):
    if fromImage.find('/') == -1:
      owner = 'library'
      image = fromImage
    else:
      owner, image = fromImage.split('/')
    URL = 'https://registry.hub.docker.com/v2/repositories/{}/{}/tags'.format(owner, image)
    response = requests.get(URL).text
    responseYaml = yaml.load(response, Loader=yaml.SafeLoader)
    newestTag = None
    newestVersion = None
    for upload in responseYaml['results']:
      if re.match(r'{}'.format(tagfilter), upload['name']):
        if newestVersion == None:
          newestTag = upload['name']
          newestVersion = datetime.datetime.strptime(upload['last_updated'], '%Y-%m-%dT%H:%M:%S.%fZ')
        else:
          tmp = datetime.datetime.strptime(upload['last_updated'], '%Y-%m-%dT%H:%M:%S.%fZ')
          if tmp > newestVersion:
            newestTag = upload['name']
            newestVersion = tmp
    self.log.info('Registry latest image: {}, tag: {}, upload: {}'.format(image, newestTag, newestVersion))
    return newestTag

  def convertDebPackagesSectionToYaml(self, section):
    section = section.replace('\n ', '')
    newSection = list()
    for line in section.split('\n'):
      newSection.append(re.sub(r'(?<=\: )(.*?)\: ', '', line))
    return yaml.load('\n'.join(newSection), Loader=yaml.SafeLoader)

  def getDebRepositoryLatestVersion(self, repository, packageName):
    repository = repository.split()
    URL = '{}/dists/{}'.format(repository[1], repository[2])
    packages = list()
    for i in range(3, len(repository)):
      packagesURL = '{}/{}/binary-amd64/Packages.gz'.format(URL, repository[i])
      data = zlib.decompress(requests.get(packagesURL).content, 16+zlib.MAX_WBITS).decode('UTF-8')
      for package in filter(None, data.split('\n\n')):
        packageDetails = self.convertDebPackagesSectionToYaml(package)
        if packageDetails['Package'] == packageName:
          packages.append(packageDetails)
    version = sorted(packages, key = lambda x:Dpkg.compare_versions_key(x['Version']), reverse=True)
    version = version[0]['Version']
    self.log.info('Package: {}, latest version: {}'.format(packageName, version))
    return version

  def checkAndUpdateDebImage(self, image, entrypointfile, templateVersion, user):
    dir = image['name']
    dockerfilecontent = image['dockerfilecontent']
    entrypointcontent = image['entrypointcontent']
    repository = image['repository']
    package = image['package']
    latestVersion = self.getDebRepositoryLatestVersion(repository, package)
    change = False
    currentContent = self.getFile(dir, 'Dockerfile-hub')
    currentEntrypoint = self.getFile(dir, entrypointfile)
    fromValue = self.getFromVariables(dockerfilecontent)[0]
    fromImage, fromVersion = self.getFromDecode(fromValue)
    dockerfilecontent = dockerfilecontent.replace('REPLACE_REPOSITORY', image['repository'])
    dockerfilecontentlocal = dockerfilecontent.replace(fromValue, 'FROM {}:{}'.format(fromImage, templateVersion))
    dockerfilecontenthub = dockerfilecontent.replace(fromValue, 'FROM {}/{}:{}'.format(user, fromImage, templateVersion))
    if dockerfilecontenthub != currentContent:
      self.log.info('Updating image')
      self.log.info('DIFF:\n{}'.format(self.getMultilineDiff(currentContent, dockerfilecontenthub)))
      self.overwriteFile(dir, 'Dockerfile-local', dockerfilecontentlocal)
      self.overwriteFile(dir, 'Dockerfile-hub', dockerfilecontenthub)
      change = True
    if entrypointcontent != currentEntrypoint:
      self.log.info('Updating entrypoint')
      self.log.info('DIFF:\n{}'.format(self.getMultilineDiff(currentEntrypoint, entrypointcontent)))
      self.overwriteFile(dir, entrypointfile, entrypointcontent)
      change = True
    if change:
      version = re.sub(r'^.*:', '', latestVersion)
      self.checkAndUpdateVersionFile(dir, version)
      self.commitAndTag('{}_{}'.format(dir, version))


  def main(self):
    for template in self.images['templates']:
      templateVersion = self.checkAndUpdateVersionsRegistry(template, 'Dockerfile')
      if 'images' in template:
        for image in template['images']:
          self.checkAndUpdateDebImage(image, 'entrypoint.sh', templateVersion, self.images['config']['user'])

if __name__ == '__main__':
  main().main()
