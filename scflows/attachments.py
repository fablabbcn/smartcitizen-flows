'''
Files attached to hardware (certificates, calibration reports...), in the same storage as the backups:
attachments/hardware/<name>/<random>-<filename>. The database keeps their name, size and who added them.
Files stay when their hardware is deleted (a hardware created again with that name shows them)
'''
import mimetypes
import re
import secrets

from . import db
from .models import Attachment
from .storage import filesystem, root

MAX_BYTES = 20 * 1024 * 1024
EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg', '.csv', '.xlsx', '.txt')


class AttachmentError(ValueError):
    pass


def safe_filename(filename):
    ''' The name of an uploaded file without folders or unusual characters, e.g. "Calibration report.pdf" '''
    name = re.split(r'[\\/]', filename or '')[-1].strip()
    name = re.sub(r'[^\w .()+-]', '_', name)[:200].strip(' .')
    return name


def attachments_of(hardware):
    return db.session.execute(db.select(Attachment).filter_by(hardware=hardware)
                              .order_by(Attachment.created_at.desc(), Attachment.id.desc())).scalars().all()


def add(hardware, filename, content, description='', identity=None):
    ''' Stores a file for a hardware. content: bytes. Raises AttachmentError if it is not accepted '''
    name = safe_filename(filename)
    if not name or not name.lower().endswith(EXTENSIONS):
        raise AttachmentError(f'file: one of {", ".join(EXTENSIONS)}')
    if not content:
        raise AttachmentError('file: empty')
    if len(content) > MAX_BYTES:
        raise AttachmentError(f'file: {MAX_BYTES // (1024 * 1024)} MB at most')
    description = (description or '').strip()
    if len(description) > 255:
        raise AttachmentError('description: 255 characters at most')
    location = f'{root()}/attachments/hardware/{hardware}/{secrets.token_hex(8)}-{name}'
    fs, path = filesystem(location)
    if not location.startswith('s3://'):
        fs.create_dir(path.rsplit('/', 1)[0], recursive=True)
    with fs.open_output_stream(path) as stream:
        stream.write(content)
    item = Attachment(hardware=hardware, filename=name, size=len(content), location=location,
                      content_type=mimetypes.guess_type(name)[0] or 'application/octet-stream',
                      description=description, user_id=identity.id if identity else None,
                      username=identity.username if identity else None)
    db.session.add(item)
    return item


def read(item):
    fs, path = filesystem(item.location)
    with fs.open_input_stream(path) as stream:
        return stream.read()


def remove(item):
    ''' Deletes the file and its record (a file already missing from the storage is not an error) '''
    fs, path = filesystem(item.location)
    try:
        fs.delete_file(path)
    except (FileNotFoundError, OSError):
        pass
    db.session.delete(item)


def find(hardware, attachment_id):
    item = db.session.get(Attachment, attachment_id)
    return item if item is not None and item.hardware == hardware else None
