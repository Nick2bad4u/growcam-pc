---
sidebar_position: 6
title: Security
description: Keep the camera and unauthenticated dashboard private.
---

# Security

The tested camera accepted a factory `admin` account with an empty password over RTSP and DVRIP. If the firmware permits it, set a strong password and place the camera on an isolated IoT network.

## Local dashboard boundary

GrowCam PC binds to `127.0.0.1` by default. The dashboard has no user-authentication layer, so a non-loopback bind is rejected unless you explicitly accept the exposure:

```shell
growcam --host 192.168.1.50 web --listen 0.0.0.0 --allow-remote
```

That command exposes camera controls and video to devices that can reach the computer. Use it only on a trusted network with an appropriate host firewall.

## Credentials

The camera's local RTSP/DVRIP account is separate from the VIVOSUN cloud account. Do not reuse the VIVOSUN account password. Prefer `GROWCAM_PASSWORD` over a password command argument so the value does not enter shell history. GrowCam PC does not send credentials to a hosted service.

## Report a vulnerability

Follow the private reporting instructions in the repository's [security policy](https://github.com/Nick2bad4u/growcam-pc/security/policy). Do not include camera passwords, private addresses, recordings, or device serial numbers in a public issue.
