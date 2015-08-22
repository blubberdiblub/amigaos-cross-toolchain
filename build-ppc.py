#!/usr/bin/env python

# Build cross toolchain for AmigaOS 4.x / PowerPC target.

from argparse import ArgumentParser
from contextlib import contextmanager
from fnmatch import fnmatch
from glob import glob
from logging import debug, info, error
from os import getcwd, environ, path
import distutils.spawn
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib2
import zipfile

URLS = \
  ['ftp://ftp.gnu.org/gnu/gmp/gmp-5.1.3.tar.bz2',
   'ftp://ftp.gnu.org/gnu/mpc/mpc-1.0.3.tar.gz',
   'ftp://ftp.gnu.org/gnu/mpfr/mpfr-3.1.3.tar.bz2',
   'http://isl.gforge.inria.fr/isl-0.12.2.tar.bz2',
   'http://www.bastoul.net/cloog/pages/download/cloog-0.18.4.tar.gz',
   'http://soulsphere.org/projects/lhasa/lhasa-0.3.0.tar.gz',
   ('http://hyperion-entertainment.biz/index.php/downloads' +
    '?view=download&amp;format=raw&amp;file=69', 'SDK_53.24.lha'),
   ('svn://svn.code.sf.net/p/adtools/code/trunk/binutils', 'binutils-2.18'),
   ('svn://svn.code.sf.net/p/adtools/code/trunk/gcc', 'gcc-4.2.4'),
   ('svn://svn.code.sf.net/p/adtools/code/branches/binutils/2.23.2',
    'binutils-2.23.2'),
   ('svn://svn.code.sf.net/p/adtools/code/branches/gcc/4.9.x', 'gcc-4.9.1'),
   ('http://github.com/adtools/sfdc/archive/master.zip', 'sfdc-master.zip'),
   ('http://clib2.cvs.sourceforge.net/viewvc/clib2/?view=tar', 'clib2.tar.gz')]

VARS = {
  'lha': 'lhasa-0.3.0',
  'gmp': 'gmp-5.1.3',
  'mpfr': 'mpfr-3.1.3',
  'mpc': 'mpc-1.0.3',
  'isl': 'isl-0.12.2',
  'cloog': 'cloog-0.18.4',
  'binutils': 'binutils-{binutils_ver}',
  'gcc': 'gcc-{gcc_ver}',
  'patches': path.join('{top}', 'patches'),
  'stamps': path.join('{top}', 'stamps'),
  'build': path.join('{top}', 'build'),
  'sources': path.join('{top}', 'sources'),
  'host': path.join('{top}', 'host'),
  'target': path.join('{top}', 'target'),
  'archives': path.join('{top}', 'archives/ppc'),
}


def fill_in(value):
  if type(value) == str:
    return value.format(**VARS)
  return value


def fill_in_args(fn):
  def wrapper(*args, **kwargs):
    args = list(fill_in(arg) for arg in args)
    kwargs = dict((key, fill_in(value)) for key, value in kwargs.items())
    return fn(*args, **kwargs)
  return wrapper


def flatten(*args):
  queue = list(args)

  while queue:
    item = queue.pop(0)
    if type(item) == list:
      queue = item + queue
    elif type(item) == tuple:
      queue = list(item) + queue
    else:
      yield item


chdir = fill_in_args(os.chdir)
path.exists = fill_in_args(path.exists)
path.join = fill_in_args(path.join)


@fill_in_args
def panic(*args):
  error(*args)
  sys.exit(1)


@fill_in_args
def cmpver(op, v1, v2):
  assert op in ['eq', 'lt', 'gt']

  v1 = [int(x) for x in v1.split('.')]
  v2 = [int(x) for x in v2.split('.')]

  def _cmp(l1, l2):
    if not len(l1) and not len(l2):
      return 0
    if not len(l1):
      return -1
    if not len(l2):
      return 1

    if l1[0] < l2[0]:
      return -1
    if l1[0] > l2[0]:
      return 1
    if l1[0] == l2[0]:
      return _cmp(l1[1:], l2[1:])

  res = _cmp(v1, v2)

  return ((op == 'eq' and res == 0) or
          (op == 'lt' and res < 0) or
          (op == 'gt' and res > 0))


@fill_in_args
def relpath(name):
  if not path.isabs(name):
    name = path.abspath(name)
  return path.relpath(name, fill_in('{top}'))


@fill_in_args
def find_executable(name):
  return (distutils.spawn.find_executable(name) or
          panic('Executable "%s" not found!', name))


@fill_in_args
def find_files(pattern):
  found = []
  for root, dirs, files in os.walk('.', topdown=True):
    for name in files:
      if fnmatch(name, pattern):
        found.append(path.join(root, name))
  return found


@fill_in_args
def touch(name):
  try:
    os.utime(name, None)
  except:
    open(name, 'a').close()


@fill_in_args
def rmtree(*names):
  for name in flatten(names):
    if path.isdir(name):
      debug('rmtree "%s"', relpath(name))
      shutil.rmtree(name)


@fill_in_args
def remove(*names):
  for name in flatten(names):
    if path.isfile(name):
      debug('remove "%s"', relpath(name))
      os.remove(name)


@fill_in_args
def mkdir(*names):
  for name in flatten(names):
    debug('makedir "%s"', relpath(name))
    os.makedirs(name)


@fill_in_args
def copytree(src, dst, **kwargs):
  debug('copytree "%s" to "%s"', relpath(src), relpath(dst))
  shutil.copytree(src, dst, **kwargs)


@fill_in_args
def rename(src, dst):
  debug('rename "%s" to "%s"', relpath(src), relpath(dst))
  os.rename(src, dst)


@fill_in_args
def symlink(src, name):
  debug('symlink "%s" from "%s"', src, relpath(name))
  os.symlink(src, name)


@fill_in_args
def execute(*cmd):
  debug('execute "%s"', " ".join(cmd))
  try:
    subprocess.check_call(cmd)
  except subprocess.CalledProcessError as ex:
    panic('command "%s" failed with %d', " ".join(list(ex.cmd)), ex.returncode)


@fill_in_args
def download(url, name):
  u = urllib2.urlopen(url)
  meta = u.info()
  size = int(meta.getheaders('Content-Length')[0])
  info('download: %s (size: %d)' % (name, size))

  with open(name, 'wb') as f:
    done = 0
    block = 8192
    while True:
      buf = u.read(block)
      if not buf:
        break
      done += len(buf)
      f.write(buf)
      status = r"%10d  [%3.2f%%]" % (done, done * 100. / size)
      status = status + chr(8) * (len(status) + 1)
      print status,

  print ""


@fill_in_args
def unarc(name):
  info('extract files from "%s"' % relpath(name))

  if name.endswith('.lha'):
    execute('lha', '-xq', name)
  else:
    if name.endswith('.tar.gz') or name.endswith('.tar.bz2'):
      module = tarfile
    elif name.endswith('.zip'):
      module = zipfile
    else:
      raise RuntimeError('Unrecognized archive: "%s"', name)

    arc = module.open(name, 'r')
    for item in arc:
      debug('extract "%s"' % item.name)
      arc.extract(item)
    arc.close()


@contextmanager
def cwd(name):
  old = getcwd()
  if not path.exists(name):
    mkdir(name)
  try:
    debug('enter directory "%s"', relpath(name))
    chdir(name)
    yield
  finally:
    chdir(old)


@contextmanager
def env(**kwargs):
  backup = {}
  try:
    for key, value in kwargs.items():
      debug('changing environment variable "%s" to "%s"', key, value)
      old = environ.get(key, None)
      environ[key] = fill_in(value)
      backup[key] = old
    yield
  finally:
    for key, value in backup.items():
      debug('restoring old value of environment variable "%s"', key)
      if value is None:
        del environ[key]
      else:
        environ[key] = value


def check_stamp(fn):
  def wrapper(*args, **kwargs):
    name = fn.func_name.replace('_', '-')
    if len(args) > 0:
      name = name + "-" + str(args[0])
    stamp = path.join('{stamps}', name)
    if not path.exists('{stamps}'):
      mkdir('{stamps}')
    if not path.exists(stamp):
      fn(*args, **kwargs)
      touch(stamp)
    else:
      info('already done "%s"', name)

  return wrapper


@check_stamp
def fetch(name, url):
  if url.startswith('http') or url.startswith('ftp'):
    if not path.exists(name):
      download(url, name)
    else:
      info('File "%s" already downloaded.', name)
  elif url.startswith('svn'):
    if not path.exists(name):
      execute('svn', 'checkout', url, name)
    else:
      execute('svn', 'update', name)


@check_stamp
def source(name, copy=None):
  try:
    src = glob(path.join('{archives}', name) + '*')[0]
  except IndexError:
    panic('Missing source for "%s".', src)

  dst = path.join('{sources}', name)
  rmtree(dst)

  info('preparing source "%s"', name)

  with cwd('{sources}'):
    if path.isdir(src):
      copytree(src, dst, ignore=shutil.ignore_patterns('.svn'))
    else:
      unarc(src)

    if copy is not None:
      rmtree(copy)
      copytree(dst, copy)


@check_stamp
def configure(name, *confopts):
  info('configuring "%s"', name)

  with cwd(path.join('{build}', name)):
    remove(find_files('config.cache'))
    execute(path.join('{sources}', name, 'configure'), *confopts)


@check_stamp
def build(name, *confopts):
  info('building "%s"', name)

  with cwd(path.join('{build}', name)):
    execute('make')


@check_stamp
def install(name, *confopts):
  info('installing "%s"', name)

  with cwd(path.join('{build}', name)):
    execute('make', 'install')


@check_stamp
def prepare_sdk():
  info('preparing SDK')

  base = ''
  clib2 = ''
  newlib = ''

  with cwd('{sources}'):
    for arc in ['base.lha', 'clib2-*.lha', 'newlib-*.lha']:
      info('extracting "%s"' % arc)
      execute('lha', '-xifq', path.join('{archives}', 'SDK_53.24.lha'),
              path.join('SDK_Install', arc))
    base = path.join('{sources}', glob('base*.lha')[0])
    clib2 = path.join('{sources}', glob('clib2*.lha')[0])
    newlib = path.join('{sources}', glob('newlib*.lha')[0])

  with cwd(path.join('{target}', 'ppc-amigaos/SDK')):
    execute('lha', '-xf', clib2, 'clib2/*')
    execute('lha', '-xf', newlib, 'newlib/*')
    execute('lha', '-xf', base, 'Include/*')
    rename('Include', 'include')


def doit():
  for var in environ.keys():
    if var not in ['_', 'LOGNAME', 'HOME', 'SHELL', 'TMPDIR', 'PWD']:
      del environ[var]

  environ['PATH'] = '/usr/bin:/bin'
  environ['LANG'] = 'C'
  environ['TERM'] = 'xterm'

  if platform.system() == 'Darwin':
    cc, cxx = 'clang', 'clang++'
  else:
    cc, cxx = 'gcc', 'g++'

  environ['CC'] = find_executable(cc)
  environ['CXX'] = find_executable(cxx)

  find_executable('bison')
  find_executable('flex')
  find_executable('make')
  find_executable('svn')

  environ['PATH'] = ":".join([path.join('{target}', 'bin'),
                              path.join('{host}', 'bin'),
                              environ['PATH']])

  with cwd('{archives}'):
    for url in URLS:
      if type(url) == tuple:
        url, name = url[0], url[1]
      else:
        name = path.basename(url)
      fetch(name, url)

  lha = VARS['lha']
  source(lha, copy=path.join('{build}', lha))
  configure(lha,
            '--disable-shared',
            '--prefix={host}')
  build(lha)
  install(lha)

  gmp = VARS['gmp']
  source(gmp)
  configure(gmp,
            '--disable-shared',
            '--prefix={host}')
  build(gmp)
  install(gmp)

  mpfr = VARS['mpfr']
  source(mpfr)
  configure(mpfr,
            '--disable-shared',
            '--prefix={host}',
            '--with-gmp={host}')
  build(mpfr)
  install(mpfr)

  mpc = VARS['mpc']
  source(mpc)
  configure(mpc,
            '--disable-shared',
            '--prefix={host}',
            '--with-gmp={host}',
            '--with-mpfr={host}')
  build(mpc)
  install(mpc)

  isl = VARS['isl']
  source(isl)
  configure(isl,
            '--disable-shared',
            '--prefix={host}',
            '--with-gmp-prefix={host}')
  build(isl)
  install(isl)

  cloog = VARS['cloog']
  source(cloog)
  configure(cloog,
            '--disable-shared',
            '--prefix={host}',
            '--with-isl=system',
            '--with-gmp-prefix={host}',
            '--with-isl-prefix={host}')
  build(cloog)
  install(cloog)

  binutils = VARS['binutils']
  binutils_env = {}
  if cmpver('eq', '{binutils_ver}', '2.18'):
    binutils_env.update(CFLAGS='-Wno-error')
  elif cmpver('eq', '{binutils_ver}', '2.23.2'):
    binutils_env.update(CFLAGS='-Wno-error')

  source(binutils)
  with env(**binutils_env):
    configure(binutils,
              '--prefix={target}',
              '--target=ppc-amigaos')
    build(binutils)
    install(binutils)

  prepare_sdk()

  gcc = VARS['gcc']
  gcc_env = {}
  if cmpver('eq', '{gcc_ver}', '4.2.4'):
    cflags = ['-std=gnu89']
    if platform.machine() == 'x86_64':
      cflags.append('-m32')
    gcc_env.update(CFLAGS=' '.join(cflags))

  source(gcc)
  with env(**gcc_env):
    configure(gcc,
              '--with-bugurl="http://sf.net/p/adtools"',
              '--target=ppc-amigaos',
              '--with-gmp={host}',
              '--with-mpfr={host}',
              '--with-cloog={host}',
              '--prefix={target}',
              '--enable-languages=c,c++',
              '--enable-haifa',
              '--enable-sjlj-exceptions'
              '--disable-libstdcxx-pch'
              '--disable-tls')
    build(gcc)
    install(gcc)


def clean():
  rmtree('{stamps}')
  rmtree('{sources}')
  rmtree('{host}')
  rmtree('{build}')


if __name__ == "__main__":
  logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

  parser = ArgumentParser(description='Build cross toolchain.')
  parser.add_argument('action', choices=['doit', 'clean'], default='doit',
                      help='perform action')
  parser.add_argument('--binutils', choices=['2.18', '2.23.2'], default='2.18',
                      help='desired binutils version')
  parser.add_argument('--gcc', choices=['4.2.4', '4.9.1'], default='4.2.4',
                      help='desired gcc version')
  parser.add_argument('--prefix', type=str, default=None,
                      help='installation directory')
  args = parser.parse_args()

  if platform.system() not in ['Darwin', 'Linux', 'Windows']:
    panic('Build on %s not supported!', platform.system())

  if platform.machine() not in ['i386', 'i686', 'x86_64']:
    panic('Build on %s architecture not supported!', platform.machine())

  VARS.update({
    'top': path.abspath('.'),
    'binutils_ver': args.binutils,
    'gcc_ver': args.gcc
  })

  if args.prefix is not None:
    VARS['target'] = args.prefix

  for key, value in VARS.items():
    VARS[key] = fill_in(value)

  if not path.exists('{target}'):
    mkdir('{target}')

  eval(args.action + "()")