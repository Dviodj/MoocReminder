"""Build an offline dependency bundle from checksum-verified official wheels."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
PACKAGES = {'PySide6-Essentials':'6.12.0','shiboken6':'6.12.0','pythonnet':'3.2.1',
    'clr_loader':'0.3.1','cffi':'2.1.1','pycparser':'3.11'}
TAGS = ['cp311','cp312','cp313','cp314','cp315']


def choose_wheel(files, name, tag=None):
    wheels = [item for item in files if item['filename'].endswith('.whl')]
    if name == 'cffi':
        return next(item for item in wheels if '-'+tag+'-'+tag+'-win_amd64.whl' in item['filename'])
    if name in ('PySide6-Essentials','shiboken6'):
        return next(item for item in wheels if 'win_amd64.whl' in item['filename'])
    return next(item for item in wheels if item['filename'].endswith('-none-any.whl'))


def fetch(item, cache):
    target = cache/item['filename']
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == item['digests']['sha256']:
        return target
    legacy = ROOT.parent/'mooc_deadline_reminder/.native-wheels'/item['filename']
    if legacy.is_file() and hashlib.sha256(legacy.read_bytes()).hexdigest() == item['digests']['sha256']:
        shutil.copyfile(legacy,target)
        return target
    temporary = target.with_suffix('.download')
    request=urllib.request.Request(item['url'],headers={'User-Agent':'MoocReminder-bundle'})
    digest=hashlib.sha256()
    with urllib.request.urlopen(request,timeout=30) as response, temporary.open('wb') as output:
        while True:
            block=response.read(1024*1024)
            if not block:break
            digest.update(block);output.write(block)
    if digest.hexdigest()!=item['digests']['sha256']:
        raise RuntimeError('Official wheel checksum did not match: '+item['filename'])
    temporary.replace(target)
    return target


def extract(wheel, destination, native=None, common_cffi=False):
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            path=PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name:
                raise RuntimeError('Unsafe wheel path')
            if name.endswith('/'):
                continue
            if native is not None:
                if name.endswith('.pyd'):
                    output=native/Path(name).name
                elif common_cffi:
                    output=destination/Path(*path.parts)
                else:
                    continue
            else:
                output=destination/Path(*path.parts)
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_bytes(archive.read(name))


def build():
    cache=ROOT/'.bundle-downloads';cache.mkdir(exist_ok=True)
    bundle=ROOT/'.runtime-bundle';common=bundle/'common'
    manifest=bundle/'bundle.json'
    # No completed marker is exposed while a bundle is being prepared.
    manifest.unlink(missing_ok=True)
    packages=[]
    for name, version in PACKAGES.items():
        print('Preparing',name,version,flush=True)
        with urllib.request.urlopen('https://pypi.org/pypi/'+name+'/'+version+'/json',timeout=20) as response:
            metadata=json.load(response)
        tags=TAGS if name=='cffi' else [None]
        for tag in tags:
            item=choose_wheel(metadata['urls'],name,tag)
            wheel=fetch(item,cache)
            extract(wheel,common,native=bundle/'native'/tag if tag else None,common_cffi=tag==TAGS[0])
            packages.append(dict(package=name,version=version,filename=item['filename'],sha256=item['digests']['sha256']))
    manifest.parent.mkdir(parents=True,exist_ok=True)
    manifest.write_text(json.dumps(dict(platform='win_amd64',python_tags=TAGS,packages=packages),indent=2),encoding='utf-8')
    size=sum(p.stat().st_size for p in bundle.rglob('*') if p.is_file())
    print('Offline runtime ready:',round(size/1024/1024,1),'MiB unpacked',flush=True)


if __name__=='__main__':
    build()
