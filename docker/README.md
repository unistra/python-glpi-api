# Docker

Generate certificates:

```
$ mkdir -p certs
$ openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout ./certs/nginx.key -out ./certs/nginx.crt
```
