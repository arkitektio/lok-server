FROM python:3.8
LABEL maintainer="jhnnsrs@gmail.com"


# Install dependencies
RUN pip install poetry rich

# Configure poetry
RUN poetry config virtualenvs.create false 
ENV PYTHONUNBUFFERED=1


# Copy dependencies
COPY pyproject.toml /
COPY poetry.lock /
RUN poetry install


# Install Arbeid
RUN mkdir /workspace
ADD . /workspace
WORKDIR /workspace


CMD bash run.sh

