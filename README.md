# Charmed Ory Hydra

## Description

Python Operator for Ory Hydra - a scalable, security first OAuth 2.0 and OpenID Connect server. For more details and documentation, visit https://www.ory.sh/docs/hydra/

## Usage

```bash
juju deploy postgresql-k8s --channel edge --trust
juju deploy hydra
juju relate postgresql-k8s hydra
```

You can follow the deployment status with `watch -c juju status --color`.

## Relations

### PostgreSQL

This charm requires a relation with [postgresql-k8s-operator](https://github.com/canonical/postgresql-k8s-operator).

### Ingress

The Hydra Operator offers integration with the [traefik-k8s-operator](https://github.com/canonical/traefik-k8s-operator) for ingress. Hydra has two APIs which can be exposed through ingress, the public API and the admin API.

If you have traefik deployed and configured in your hydra model, to provide ingress to the admin API run:

```bash
juju relate traefik-admin hydra:admin-ingress
```

To provide ingress to the public API run:

```bash
juju relate traefik-public hydra:public-ingress
```

### Kratos

This charm offers integration with [kratos-operator](https://github.com/canonical/kratos-operator).


## Integration with Kratos and UI

The following instructions assume that you have deployed `traefik-admin` and `traefik-public` charms and related them to hydra.

If you have deployed [Login UI charm](https://github.com/canonical/identity-platform-login-ui), you can configure it with hydra by providing its URL.
Note that the UI charm should run behind a proxy.
```console
juju config hydra login_ui_url=http://{traefik_public_ip}/{model_name}-{kratos_ui_app_name}
```

In order to integrate hydra with kratos, it needs to be able to access hydra's admin API endpoint.
To enable that, relate the two charms:
```console
juju relate kratos hydra
```

For further guidance on integration on kratos side, visit the [kratos-operator](https://github.com/canonical/kratos-operator#readme) repository.

## OCI Images

The image used by this charm is hosted on [Docker Hub](https://hub.docker.com/r/oryd/hydra) and maintained by Ory.
