# Lok

[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/arkitektio/lok-server/)
![Maintainer](https://img.shields.io/badge/maintainer-jhnnsrs-blue)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)


Lok is a central backend to manage and authorize User and Applications in a distributed
settings. Loks provides endpoints for apps to configure themselvers (through the Fakts protocol)
and in a second step to authenticate and authorize users. For the latter it is build on top of [Oauth2](https://oauth.net/2/)
and [OpenID Connect](https://openid.net/connect/). It then provides a central authentication and authorization
service for applications to register and authenticate users, and issues JWT token for accessing services.

As JWT are cryptographically signed, they can be verified by any service, and do not require
a central session store. 

This distributed and scalable authentication and authorization system, was developed as the backbone for the
Arkitekt platform, but can be used as a standalone service for any application.

## Features

- [x] Application Registration (Authentication of apps based on various Flows)
- [x] App Configuration (apps can retrieve their configuration from the server)
- [x] User Authentication and Authorization
- [x] User and Application Management
- [x] Distibuted Authentication
- [x] Social Features (Comments) 
- [x] User Profiles

All features are exposed through a GraphQL API, which can be used to interact with the system.


## Next Features

Lok is currently undergoing a major rewrite, to make it more modular and easier to extend. This rewrite
will transition the system to a more modular architecture based on modern [Django](https://www.djangoproject.com/) and
the awesome [Strawberry GraphQL](https://strawberry.rocks/) library.

Additionally to the listed
features above, the following features are planned:

- [ ] More diverse App Registration Flows (e.g. for Websites)
- [ ] Social Login (Login with Orcid, Github, Google,... )
- [ ] User Profiles with social account information
- [ ] Notificaition Backend (with Mobile Push Notifications)
- [ ] More Security Features (e.g. 2FA)


While this rewrite is ongoing, the current version of Lok will remain the main repository for Lok, and the new version will be merged into this repository once the new version is ready for production. Development is happening in the [lok-server-next](https://gihtub.com/arkitektio/lok-server-next) repository.