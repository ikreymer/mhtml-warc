from email.parser import BytesParser
import email.policy
import email
import json

from warcio.warcwriter import BufferWARCWriter, WARCWriter, BaseWARCWriter
from warcio.statusandheaders import StatusAndHeaders
from warcio.timeutils import iso_date_to_timestamp, http_date_to_datetime, datetime_to_iso_date, datetime_to_timestamp

from collections import OrderedDict

import logging
import sys

from io import StringIO, BytesIO


__version__ = '1.0.0'

allow_cid_urls = False


# ============================================================================
class MHTML2WARC:
    logger = logging.getLogger(__name__)

    def __init__(self, writer, gzip=True):
        self.fh = None
        self.writer = None
        self.filename = 'unknown'
        self.is_first = True

        if isinstance(writer, BaseWARCWriter):
            self.writer = writer
        elif isinstance(writer, str):
            self.fh = open(writer, 'wb')
            self.filename = writer
            self.writer = WARCWriter(self.fh, gzip=gzip)
        elif hasattr(writer, 'write'):
            self.writer = WARCWriter(writer, gzip=gzip)
        else:
            raise Exception('writer is in an unknown format')

    def parse(self, input_):
        if isinstance(input_, str):
            with open(input_, 'rb') as rfh:
                message = email.message_from_binary_file(rfh, policy=email.policy.strict)
        elif hasattr(input_, 'read'):
            message = email.message_from_binary_file(input_, policy=email.policy.strict)
        else:
            raise Exception('input is in an unknown format')

        if not message.is_multipart():
            raise Exception('Invalid MHTML -- not multipart')


        main_url = message.get('Snapshot-Content-Location', '')

        warc_date = self.write_warc_info(message)

        for part in message.walk():
            if part.get_content_type() == 'multipart/related':
                continue

            self.write_resource(part, main_url, warc_date)

    def write_resource(self, part, main_url, warc_date):
        content_type = part.get_content_type()
        main_type = part.get_content_maintype()
        content = part.get_payload(decode=True)

        url = part.get('Content-Location')

        warc_headers = {'WARC-Date': warc_date,
                        'WARC-Creation-Date': self.writer._make_warc_date(),
                       }

        content_id = part.get('Content-ID')
        write_redir = False

        if content_id:
            warc_headers['Content-ID'] = content_id

            cid_url = 'cid:' + content_id[1:-1]

            # only write main page url once under url
            # there may be additional frames for same url
            # only write them under cid
            if url == main_url:
                if self.is_first:
                    self.is_first = False
                else:
                    url = None

            if not url:
                # if cid urls not allowed, skip this resource
                if not allow_cid_urls:
                    return
                url = cid_url
            else:
                write_redir = True


        record = self.writer.create_warc_record(url, 'resource',
                                  payload=BytesIO(content),
                                  length=len(content),
                                  warc_content_type=content_type,
                                  warc_headers_dict=warc_headers)

        self.writer.write_record(record)

        if write_redir and allow_cid_urls:
            self.add_cid_redirect(cid_url, url)

    def add_cid_redirect(self, cid_url, url):
        msg = b'redirect'

        headers_list = [('Content-Type', 'text/plain'),
                        ('Content-Length', str(len(msg))),
                        ('Location', url)]

        http_headers = StatusAndHeaders('302 Redirect', headers_list, protocol='HTTP/1.0')

        record = self.writer.create_warc_record(cid_url, 'response',
                                  length=len(msg),
                                  payload=BytesIO(msg),
                                  http_headers=http_headers)

        self.writer.write_record(record)

    def write_warc_info(self, message):
        creator = message.get('From', '')

        url = message.get('Snapshot-Content-Location', '')

        title = message.get('Subject', url)


        try:
            actual_date = http_date_to_datetime(message['Date'])
            timestamp = datetime_to_timestamp(actual_date)
        except Exception:
            actual_date = ''
            timestamp = ''

        source = 'MHTML Snapshot for: ' + url

        software = 'mhtml2warc ' + str(__version__)

        metadata = {'title':  source,
                    'type': 'recording',
                    'pages': [{'title': title,
                               'url': url,
                               'timestamp': timestamp}]
                   }

        params = OrderedDict([('software', software),
                              ('creator', creator),
                              ('source', source),
                              ('format', 'WARC File Format 1.0'),
                              ('subject', title),
                              ('json-metadata', json.dumps(metadata))])

        record = self.writer.create_warcinfo_record(self.filename, params)

        if actual_date:
            actual_date = datetime_to_iso_date(actual_date)

            creation_date = record.rec_headers.get('WARC-Date')
            record.rec_headers.replace_header('WARC-Date', actual_date)
            record.rec_headers.replace_header('WARC-Creation-Date', creation_date)

        self.writer.write_record(record)

        return actual_date


if __name__ == '__main__':
    MHTML2WARC(sys.argv[2]).parse(sys.argv[1])
