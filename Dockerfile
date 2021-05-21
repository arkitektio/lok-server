FROM python:3.8
LABEL maintainer="jhnnsrs@gmail.com"


# Install Minimal Dependencies for Django
ADD requirements.txt /tmp
WORKDIR /tmp
RUN pip install -r requirements.txt

# Install Arbeid
RUN mkdir /workspace
ADD . /workspace
WORKDIR /workspace

