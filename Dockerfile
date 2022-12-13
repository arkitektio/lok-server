FROM python:3.8
LABEL maintainer="jhnnsrs@gmail.com"


# Install dependencies
RUN pip install poetry rich
ENV PYTHONUNBUFFERED=1

# Copy dependencies
COPY pyproject.toml /
RUN poetry config virtualenvs.create false 
RUN poetry install


# Install Arbeid
RUN mkdir /workspace
ADD . /workspace
WORKDIR /workspace


CMD bash run.sh

