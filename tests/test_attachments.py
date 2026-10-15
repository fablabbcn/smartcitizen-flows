''' Files attached to hardware '''
from io import BytesIO

import pytest
from flask import g

from scflows import attachments, db
from scflows.metadata import import_metadata
from scflows.models import Attachment

from conftest import DATA, USERS, auth

PDF = b'%PDF-1.4 calibration report'


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv('STORAGE_ROOT', str(tmp_path / 'storage'))
    return tmp_path / 'storage'


@pytest.fixture
def client(app, storage):
    import_metadata(DATA)
    return app.test_client()


def sign_in(client, role='admin', hardware=()):
    user = next(user for user in USERS.values() if user['role'] == role)
    g.pop('_login_user', None)
    with client.session_transaction() as session:
        session['identity'] = dict(user, hardware=list(hardware), devices=[])
        session['_user_id'] = str(user['id'])
        session['csrf'] = 'token'


def upload(client, filename='Report 2026.pdf', content=PDF, description='Alphasense certificate'):
    return client.post('/metadata/hardware/SCAS_TEST1/attachments', content_type='multipart/form-data',
                       data={'csrf': 'token', 'description': description, 'file': (BytesIO(content), filename)})


def stored():
    db.session.expire_all()
    return db.session.execute(db.select(Attachment)).scalars().all()


def test_attach_download_and_delete(client, storage):
    sign_in(client)

    response = upload(client)

    assert response.status_code == 302 and response.headers['Location'].endswith('/metadata/hardware/SCAS_TEST1#attachments')
    [item] = stored()
    assert (item.filename, item.size, item.description, item.username) == ('Report 2026.pdf', len(PDF), 'Alphasense certificate', 'admin')
    assert item.location.startswith(f'{storage}/attachments/hardware/SCAS_TEST1/') and item.location.endswith('-Report 2026.pdf')
    page = client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)
    assert 'Report 2026.pdf' in page and 'Alphasense certificate' in page

    response = client.get(f'/metadata/hardware/SCAS_TEST1/attachments/{item.id}')
    assert response.data == PDF and response.mimetype == 'application/pdf'
    assert response.headers['Content-Disposition'].startswith('inline')

    client.post(f'/metadata/hardware/SCAS_TEST1/attachments/{item.id}/delete', data={'csrf': 'token'})
    assert stored() == [] and not list((storage / 'attachments' / 'hardware' / 'SCAS_TEST1').iterdir())


@pytest.mark.parametrize('filename, content, message', [
    ('script.sh', PDF, 'file: one of'),
    ('empty.pdf', b'', 'file: empty'),
])
def test_rejected_files(client, filename, content, message):
    sign_in(client)
    upload(client, filename, content)
    assert stored() == [] and message in client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)


def test_files_up_to_the_limit(client, monkeypatch):
    sign_in(client)
    monkeypatch.setattr(attachments, 'MAX_BYTES', 2 * 1024 * 1024)
    # Over the 1 MB of other requests
    assert upload(client, content=b'x' * (1024 * 1024 + 10)).status_code == 302 and len(stored()) == 1
    # Over the limit: rejected before reading it, or by the check
    assert upload(client, content=b'x' * (3 * 1024 * 1024 + 10)).status_code in (302, 413) and len(stored()) == 1


def test_names_are_made_safe():
    assert attachments.safe_filename('../../etc/passwd.pdf') == 'passwd.pdf'
    assert attachments.safe_filename('C:\\docs\\cert; rm.pdf') == 'cert_ rm.pdf'


def test_researchers_download_attachments_of_their_hardware_only(client):
    sign_in(client)
    upload(client)
    [item] = stored()

    sign_in(client, 'researcher', hardware=['SCAS_TEST1'])
    assert client.get(f'/metadata/hardware/SCAS_TEST1/attachments/{item.id}').data == PDF
    assert upload(client).status_code == 403
    assert client.post(f'/metadata/hardware/SCAS_TEST1/attachments/{item.id}/delete', data={'csrf': 'token'}).status_code == 403
    page = client.get('/metadata/hardware/SCAS_TEST1').get_data(as_text=True)
    assert 'Report 2026.pdf' in page and 'Attach</button>' not in page

    sign_in(client, 'researcher', hardware=['SCAS_TEST2'])
    assert client.get(f'/metadata/hardware/SCAS_TEST1/attachments/{item.id}').status_code == 403
    # The id of another hardware's file
    sign_in(client, 'admin')
    assert client.get(f'/metadata/hardware/SCAS_TEST2/attachments/{item.id}').status_code == 404


def test_api(client, sc_me):
    response = client.post('/api/v1/hardware/SCAS_TEST1/attachments', headers=auth('admin-token'),
                           data={'file': (BytesIO(PDF), 'cert.pdf'), 'description': 'Certificate'},
                           content_type='multipart/form-data')
    assert response.status_code == 201
    attachment_id = response.get_json()['id']

    [listed] = client.get('/api/v1/hardware/SCAS_TEST1/attachments', headers=auth('admin-token')).get_json()
    assert (listed['filename'], listed['description']) == ('cert.pdf', 'Certificate')
    assert client.get(listed['url'], headers=auth('admin-token')).data == PDF
    # Not public, unlike the hardware
    assert client.get('/api/v1/hardware/SCAS_TEST1/attachments').status_code == 401
    assert client.post('/api/v1/hardware/SCAS_TEST1/attachments', headers=auth('researcher-token'),
                       data={'file': (BytesIO(PDF), 'cert.pdf')}, content_type='multipart/form-data').status_code == 403
    assert client.post('/api/v1/hardware/SCAS_TEST1/attachments', headers=auth('admin-token')).status_code == 422
    assert client.post('/api/v1/hardware/NOPE/attachments', headers=auth('admin-token'),
                       data={'file': (BytesIO(PDF), 'cert.pdf')}, content_type='multipart/form-data').status_code == 404

    assert client.delete(f'/api/v1/hardware/SCAS_TEST1/attachments/{attachment_id}', headers=auth('admin-token')).status_code == 204
    assert stored() == []
