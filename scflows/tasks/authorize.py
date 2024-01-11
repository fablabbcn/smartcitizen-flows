# Copyright (C) 2023 Secure Dimensions GmbH, Munich, Germany.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json
import random
import time
import string
import requests

import base64
import hashlib
import logging
import secrets
import sys
import webbrowser
import jwt
from jwt import PyJWKClient
from typing import Tuple
from oauth2_client.credentials_manager import ServiceInformation
from oauth2_client.credentials_manager import CredentialManager

logging.basicConfig(stream=sys.stdout, level=logging.INFO)
_logger = logging.getLogger()

def generate_sha256_pkce(length: int) -> Tuple[str, str]:
    if not (43 <= length <= 128):
        raise Exception("Invalid length: " % str(length))
    verifier = secrets.token_urlsafe(length)
    encoded = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest())
    challenge = encoded.decode('ascii')[:-1]
    return verifier, challenge

def registerApp() -> Tuple[str, str]:
    request = {
        "redirect_uris":  ["http://127.0.0.1:4711/SensorApp"],
        "audiences": ["3042e50b-dc09-4817-b34c-1b06c709da78"],
        "grant_types": ["authorization_code", "offline_access"],
        "response_types": ["code", "code id_token"],
        "client_name": "STAplus SCK App",
        "logo_uri": "data:image/png;base64,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RGltZW5zaW9uPjc1MjwvZXhpZjpQaXhlbFlEaW1lbnNpb24+CiAgICAgIDwvcmRmOkRlc2NyaXB0aW9uPgogICA8L3JkZjpSREY+CjwveDp4bXBtZXRhPgpYQe/dAABAAElEQVR4Ae3dB7wtV10v8PQe0gvpIYRQAgGkhI5gqBHwgQrie/BQlCLgQwURkSIoiNKkqyCIVCmCNOktEGoSSiAhpCekk15veL8/nkPuvdn33nP22XvPmj3f9fn82OfM2TNrre/MCf87Z/bMRhtpBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAEC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IECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIECAAAECBAgQIEDg/wPKQ3JcQ2vwAQAAAABJRU5ErkJggg==",
        "scope": "openid profile email idp offline_access citiobs.secd.eu#create, citiobs.secd.eu#update",
        "contacts": [
          "Secure Dimensions GmbH",
          "https://www.secure-dimensions.de",
          "W 28",
          "DE",
          "https://www.secure-dimensions.de/legal"
        ],
        "operator_country": "de",
        "tos_uri": "https://www.secure-dimensions.de/terms",
        "policy_uri": "https://www.secure-dimensions.de/privacy",
        "software_id": "b8815b0ff48b66ed3adbecb5d405fb15d941dbdb",
        "software_version": "1.0",
        "application_type": "web"
    }

    register = False
    app_metadata = {}
    try:
        with open('SensorApp.json', 'r') as f:
            app_metadata = json.loads(f.read())
            if int(time.time()) >= app_metadata['expires']:
                register = True
    except FileNotFoundError:
                register = True

    if register:
        header = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        response = requests.post('https://www.authenix.eu/oauth/register', data=json.dumps(request), headers=header)
        if response.status_code != 200:
            _logger.error("App Registration error: ", response.content)

        app_metadata = response.json()
        with open('SensorApp.json', 'w') as f:
            json.dump(app_metadata, f)

    client_id = app_metadata['client_id']
    client_secret = app_metadata['client_secret']

    _logger.info("client_id :%s", client_id)
    _logger.info("client_secret :%s", client_secret)
    return client_id, client_secret

def authorize():
    scopes = ['openid', 'profile', 'citiobs.secd.eu#create', 'citiobs.secd.eu#update']
    redirect_uri = 'http://127.0.0.1:4711/SensorApp'
    (client_id, client_secret) = registerApp()
    state = ''.join(random.choice(string.ascii_letters) for m in range(10))

    service_information = ServiceInformation('https://www.authenix.eu/oauth/authorize',
        'https://www.authenix.eu/oauth/token',
        client_id,
        client_secret,
        scopes)
    manager = CredentialManager(service_information)

    code_verifier, code_challenge = generate_sha256_pkce(64)
    # Builds the authorization url and starts the local server according to the redirect_uri parameter
    kwargs = {'response_type': 'id_token code'}
    url = manager.init_authorize_code_process(redirect_uri,
        state,
        code_challenge=code_challenge,
        code_challenge_method="S256",
        **kwargs)
    _logger.info('Open this url in your browser\n%s', url)
    webbrowser.open(url, new=0, autoraise=True)

    code, id_token = manager.wait_and_terminate_authorize_code_process()

    _logger.debug('code got = %s', code)
    _logger.debug('id_token got = %s', id_token)
    kid = "www.authenix.eu#id_token"
    url = "https://www.authenix.eu/.well-known/jwks.json"
    jwks_client = PyJWKClient(url)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    data = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=client_id,
        options={"verify_exp": False, 'verify_iat': False},
    )
    _logger.info(data)
    manager.init_with_authorize_code(redirect_uri, code)
    _logger.debug('Access Token = %s', manager._access_token)
    # Here access and refresh token may be used with self.refresh_token

    return manager._access_token, data

