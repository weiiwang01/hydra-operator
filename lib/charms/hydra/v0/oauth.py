# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""# Oauth Library.

This library is designed to enable applications to register OAuth2/OIDC
clients with an OIDC Provider through the `oauth` interface.

## Getting started

To get started using this library you just need to fetch the library using `charmcraft`. **Note
that you also need to add `jsonschema` to your charm's `requirements.txt`.**

```shell
cd some-charm
charmcraft fetch-lib charms.hydra.v0.oauth
EOF
```

Then, to initialize the library:
```python
# ...
from charms.hydra.v0.kubernetes_service_patch import ClientConfig, OAuthRequirer

OAUTH = "oauth"
OAUTH_SCOPES = "openid email"
OAUTH_GRANT_TYPES = ["authorization_code"]

class SomeCharm(CharmBase):
  def __init__(self, *args):
    # ...
    self.oauth = OAuthRequirer(self, client_config, relation_name=OAUTH)

    self.framework.observe(self.oauth.on.client_credentials_changed, self._configure_application)
    self.framework.observe(self.oauth.on.provider_config_changed, self._configure_application)
    # ...

    def _on_ingress_ready(self, event):
        self.external_url = "https://example.com
        self._set_client_config()

    def _set_client_config(self):
        client_config = ClientConfig(
            join(self.external_url, "/oauth/callback"),
            OAUTH_SCOPES,
            OAUTH_GRANT_TYPES,
        )
        self.oauth.update_client_config(client_config)
```
"""

import json
import logging
import re
from dataclasses import asdict, dataclass, field

import jsonschema
from ops.framework import EventBase, EventSource, Object, ObjectEvents

# The unique Charmhub library identifier, never change it
LIBID = "a3a301e325e34aac80a2d633ef61fe97"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

logger = logging.getLogger(__name__)

DEFAULT_RELATION_NAME = "oauth"
ALLOWED_GRANT_TYPES = ["authorization_code", "refresh_token", "client_credentials"]
ALLOWED_CLIENT_AUTHN_METHODS = ["client_secret_basic", "client_secret_post"]
CLIENT_SECRET_FIELD = "secret"

url_regex = re.compile(
    r"^https://"  # https://
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|"
    r"[A-Z0-9-]{2,}\.?)|"  # domain...
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
    r"(?::\d+)?"  # optional port
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

OAUTH_PROVIDER_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "$id": "https://canonical.github.io/charm-relation-interfaces/interfaces/oauth/schemas/provider.json",
    "type": "object",
    "properties": {
        "issuer_url": {
            "type": "string",
        },
        "authorization_endpoint": {
            "type": "string",
        },
        "token_endpoint": {
            "type": "string",
        },
        "introspection_endpoint": {
            "type": "string",
        },
        "userinfo_endpoint": {
            "type": "string",
        },
        "jwks_endpoint": {
            "type": "string",
        },
        "scope": {
            "type": "string",
        },
        "client_id": {
            "type": "string",
        },
        "client_secret_id": {
            "type": "string",
        },
        "groups": {"type": "string", "default": None},
        "ca_chain": {"type": "array", "items": {"type": "string"}, "default": []},
    },
    "required": [
        "issuer_url",
        "authorization_endpoint",
        "token_endpoint",
        "introspection_endpoint",
        "userinfo_endpoint",
        "jwks_endpoint",
        "scope",
    ],
}
OAUTH_REQUIRER_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "$id": "https://canonical.github.io/charm-relation-interfaces/interfaces/oauth/schemas/requirer.json",
    "type": "object",
    "properties": {
        "redirect_uri": {
            "type": "string",
            "default": None,
        },
        "audience": {"type": "array", "default": [], "items": {"type": "string"}},
        "scope": {"type": "string", "default": None},
        "grant_types": {
            "type": "array",
            "default": None,
            "items": {
                "enum": ["authorization_code", "client_credentials", "refresh_token"],
                "type": "string",
            },
        },
        "token_endpoint_auth_method": {
            "type": "string",
            "enum": ["client_secret_basic", "client_secret_post"],
            "default": "client_secret_basic",
        },
    },
    "required": ["redirect_uri", "audience", "scope", "grant_types", "token_endpoint_auth_method"],
}


class ClientConfigError(Exception):
    """Emitted when invalid client config is provided."""

    pass


class DataValidationError(RuntimeError):
    """Raised when data validation fails on relation data."""


def _load_data(data, schema=None):
    """Parses nested fields and checks whether `data` matches `schema`."""
    ret = {}
    for k, v in data.items():
        try:
            ret[k] = json.loads(v)
        except json.JSONDecodeError as e:
            ret[k] = v

    if schema:
        _validate_data(ret, schema)
    return ret


def _dump_data(data, schema=None):
    if schema:
        _validate_data(data, schema)

    ret = {}
    for k, v in data.items():
        if isinstance(v, (list, dict)):
            try:
                ret[k] = json.dumps(v)
            except json.JSONDecodeError as e:
                raise DataValidationError(f"Failed to encode relation json: {e}")
        else:
            ret[k] = v
    return ret


def _validate_data(data, schema):
    """Checks whether `data` matches `schema`.

    Will raise DataValidationError if the data is not valid, else return None.
    """
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        raise DataValidationError(data, schema) from e


@dataclass
class ClientConfig:
    """Helper class containing a client's configuration."""

    redirect_uri: str
    scope: str
    grant_types: list[str]
    audience: list[str] = field(default_factory=lambda: [])
    token_endpoint_auth_method: str = "client_secret_basic"

    def validate(self):
        """Validate the client configuration."""
        # Validate redirect_uri
        if not re.match(url_regex, self.redirect_uri):
            raise ClientConfigError(f"Invalid URL {self.redirect_uri}")

        # Validate grant_types
        for grant_type in self.grant_types:
            if grant_type not in ALLOWED_GRANT_TYPES:
                raise ClientConfigError(
                    f"Invalid grant_type {grant_type}, must be one " f"of {ALLOWED_GRANT_TYPES}"
                )

        # Validate client authentication methods
        if self.token_endpoint_auth_method not in ALLOWED_CLIENT_AUTHN_METHODS:
            raise ClientConfigError(
                f"Invalid client auth method {self.token_endpoint_auth_method}, "
                f"must be one of {ALLOWED_CLIENT_AUTHN_METHODS}"
            )


class ClientCredentialsChangedEvent(EventBase):
    """Event to notify the charm that the client credentials changed."""

    def __init__(self, handle, client_id, client_secret_id):
        super().__init__(handle)
        self.client_id = client_id
        self.client_secret_id = client_secret_id

    def snapshot(self):
        """Save event."""
        return {
            "client_id": self.client_id,
            "client_secret_id": self.client_secret_id,
        }

    def restore(self, snapshot):
        """Restore event."""
        self.client_id = snapshot["client_id"]
        self.client_secret_id = snapshot["client_secret_id"]


class ProviderConfigChangedEvent(EventBase):
    """Event to notify the charm that the provider's configuration changed."""


class OAuthRequirerEvents(ObjectEvents):
    """Event descriptor for events raised by `OAuthRequirerEvents`."""

    client_credentials_changed = EventSource(ClientCredentialsChangedEvent)
    provider_config_changed = EventSource(ProviderConfigChangedEvent)


class OAuthRequirer(Object):
    """Register an oauth client."""

    on = OAuthRequirerEvents()

    def __init__(self, charm, client_config=None, relation_name=DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._client_config = client_config
        events = self._charm.on[relation_name]
        self.framework.observe(events.relation_created, self._on_relation_created_event)
        self.framework.observe(events.relation_changed, self._on_relation_changed_event)

    def _on_relation_created_event(self, event):
        try:
            self._update_relation_data(self._client_config, event.relation.id)
        except Exception:
            pass

    def _on_relation_changed_event(self, event):
        if not self.model.unit.is_leader():
            return

        data = _load_data(event.relation.data[event.app], OAUTH_PROVIDER_JSON_SCHEMA)

        client_id = data.get("client_id")
        client_secret_id = data.get("client_secret_id")
        if not client_id or not client_secret_id:
            # This probably means that the Provider just set its endpoint,
            # but it could also mean that the providers removed the client credentials
            # from the databag. We could do something similar to
            # data_platform_libs/v0/data_interfaces:diff if we wanted to
            # TODO
            self.on.provider_config_changed.emit()
            return

        if client_secret_id:
            self.on.client_credentials_changed.emit(client_id, client_secret_id)
        else:
            # TODO: log some error?
            pass

    def _update_relation_data(self, client_config, relation_id):
        if not self.model.unit.is_leader():
            return

        try:
            client_config.validate()
        except ClientConfigError as e:
            # emit error event
            logger.info(e)
            return

        relation = self.model.get_relation(
            relation_name=self._relation_name, relation_id=relation_id
        )

        data = _dump_data(asdict(client_config), OAUTH_REQUIRER_JSON_SCHEMA)
        relation.data[self.model.app].update(data)

    def get_provider_info(self):
        if len(self.model.relations) == 0:
            return
        relation = self.model.get_relation(self._relation_name)

        data = _load_data(relation.data[relation.app], OAUTH_PROVIDER_JSON_SCHEMA)
        data.pop("client_id", None)
        data.pop("client_secret_id", None)
        return data

    def get_client_secret(self, client_secret_id):
        client_secret = self.model.get_secret(id=client_secret_id)
        return client_secret

    def update_client_config(self, client_config):
        """Update the client config stored in the object."""
        self._client_config = client_config


class ClientCreateEvent(EventBase):
    """Event to notify the Provider charm that to create a new client."""

    def __init__(
        self,
        handle,
        redirect_uri,
        scope,
        grant_types,
        audience,
        token_endpoint_auth_method,
        relation_id,
    ):
        super().__init__(handle)
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.grant_types = grant_types
        self.audience = audience
        self.token_endpoint_auth_method = token_endpoint_auth_method
        self.relation_id = relation_id

    def snapshot(self):
        """Save event."""
        return {
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "grant_types": self.grant_types,
            "audience": self.audience,
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "relation_id": self.relation_id,
        }

    def restore(self, snapshot):
        """Restore event."""
        self.redirect_uri = snapshot["redirect_uri"]
        self.scope = snapshot["scope"]
        self.grant_types = snapshot["grant_types"]
        self.audience = snapshot["audience"]
        self.token_endpoint_auth_method = snapshot["token_endpoint_auth_method"]
        self.relation_id = snapshot["relation_id"]

    def to_client_config(self):
        """Convert the event information to a ClientConfig object."""
        return ClientConfig(
            self.redirect_uri,
            self.scope,
            self.grant_types,
            self.audience,
            self.token_endpoint_auth_method,
        )


class ClientConfigChangedEvent(EventBase):
    """Event to notify the Provider charm that the client config changed."""

    def __init__(
        self,
        handle,
        redirect_uri,
        scope,
        grant_types,
        audience,
        token_endpoint_auth_method,
        relation_id,
        client_id,
    ):
        super().__init__(handle)
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.grant_types = grant_types
        self.audience = audience
        self.token_endpoint_auth_method = token_endpoint_auth_method
        self.relation_id = relation_id
        self.client_id = client_id

    def snapshot(self):
        """Save event."""
        return {
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
            "grant_types": self.grant_types,
            "audience": self.audience,
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
            "relation_id": self.relation_id,
            "client_id": self.client_id,
        }

    def restore(self, snapshot):
        """Restore event."""
        self.redirect_uri = snapshot["redirect_uri"]
        self.scope = snapshot["scope"]
        self.grant_types = snapshot["grant_types"]
        self.audience = snapshot["audience"]
        self.token_endpoint_auth_method = snapshot["token_endpoint_auth_method"]
        self.relation_id = snapshot["relation_id"]
        self.client_id = snapshot["client_id"]

    def to_client_config(self):
        """Convert the event information to a ClientConfig object."""
        return ClientConfig(
            self.redirect_uri,
            self.scope,
            self.grant_types,
            self.audience,
            self.token_endpoint_auth_method,
        )


class OAuthProviderEvents(ObjectEvents):
    """Event descriptor for events raised by `OAuthProviderEvents`."""

    client_created = EventSource(ClientCreateEvent)
    client_config_changed = EventSource(ClientConfigChangedEvent)


class OAuthProvider(Object):
    """A provider object for OIDC Providers."""

    on = OAuthProviderEvents()

    def __init__(self, charm, relation_name=DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self._get_client_config_from_relation_data,
        )

    def _get_client_config_from_relation_data(self, event):
        if not self.model.unit.is_leader():
            return

        client_data = _load_data(event.relation.data[event.app], OAUTH_REQUIRER_JSON_SCHEMA)
        redirect_uri = client_data.get("redirect_uri")
        scope = client_data.get("scope")
        grant_types = client_data.get("grant_types")
        audience = client_data.get("audience")
        token_endpoint_auth_method = client_data.get("token_endpoint_auth_method")

        provider_data = _load_data(
            event.relation.data[self._charm.app], OAUTH_PROVIDER_JSON_SCHEMA
        )
        client_id = provider_data.get("client_id")

        relation_id = event.relation.id

        if client_id:
            # Modify an existing client
            self.on.client_config_changed.emit(
                redirect_uri,
                scope,
                grant_types,
                audience,
                token_endpoint_auth_method,
                relation_id,
                client_id,
            )
        else:
            # Create a new client
            self.on.client_created.emit(
                redirect_uri, scope, grant_types, audience, token_endpoint_auth_method, relation_id
            )

    def _create_juju_secret(self, client_secret, relation):
        """Create a juju secret and grant it to a relation."""
        secret = {CLIENT_SECRET_FIELD: client_secret}
        juju_secret = self.model.app.add_secret(secret, label="client_secret")
        juju_secret.grant(relation)
        return juju_secret

    def set_provider_info_in_relation_data(self, data):
        """Put the provider information in the the databag."""
        if not self.model.unit.is_leader():
            return

        for relation in self.model.relations[self._relation_name]:
            relation.data[self.model.app].update(_dump_data(data))

    def set_client_credentials_in_relation_data(self, relation_id, client_id, client_secret):
        """Put the client credentials in the the databag."""
        if not self.model.unit.is_leader():
            return

        relation = self.model.get_relation(self._relation_name, relation_id)
        # TODO: What if we are refreshing the client_secret? We need to add a
        # new revision for that
        secret = self._create_juju_secret(client_secret, relation)
        data = dict(client_id=client_id, client_secret_id=secret.id)
        relation.data[self.model.app].update(_dump_data(data))
