from pyarrow import fs

from scflows import storage


def test_s3_uses_the_region_of_the_bucket(monkeypatch):
    # A region in the environment that is not the one of the bucket made uploads fail
    monkeypatch.setenv('AWS_REGION', 'eu-south-1')
    monkeypatch.setattr(storage, 'REGIONS', {})
    asked = []
    monkeypatch.setattr(fs, 'resolve_s3_region', lambda bucket: asked.append(bucket) or 'eu-west-1')

    filesystem, path = storage.filesystem('s3://bucket/attachments/hardware/SCAS1/file.pdf')
    storage.filesystem('s3://bucket/devices/1')

    assert filesystem.region == 'eu-west-1'
    assert path == 'bucket/attachments/hardware/SCAS1/file.pdf'
    assert asked == ['bucket']


def test_s3_region_from_the_environment_if_s3_cannot_be_asked(monkeypatch):
    monkeypatch.setenv('AWS_REGION', 'eu-west-1')
    monkeypatch.setattr(storage, 'REGIONS', {})

    def unreachable(bucket):
        raise OSError('unreachable')

    monkeypatch.setattr(fs, 'resolve_s3_region', unreachable)
    assert storage.filesystem('s3://bucket/devices/1')[0].region == 'eu-west-1'

    # Asked again next time
    monkeypatch.setattr(fs, 'resolve_s3_region', lambda bucket: 'eu-central-1')
    assert storage.filesystem('s3://bucket/devices/1')[0].region == 'eu-central-1'


def test_local_storage(tmp_path):
    filesystem, path = storage.filesystem(str(tmp_path))

    assert isinstance(filesystem, fs.LocalFileSystem) and path == str(tmp_path)
