FROM python:3

ENV DEBIAN_FRONTEND noninteractive
RUN apt-get -y update && \
    apt-get -y install sudo && \
    apt-get clean

WORKDIR /usr/src/updater

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT [ "./entrypoint.sh" ]
