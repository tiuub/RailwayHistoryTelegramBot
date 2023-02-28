FROM python:3.9-alpine as base

FROM base as builder

RUN mkdir /install
RUN apk update && apk add postgresql-dev gcc python3-dev musl-dev
WORKDIR /install
COPY requirements.txt /requirements.txt
RUN pip3 install --prefix=/install -r /requirements.txt

FROM base

COPY --from=builder /install /usr/local
RUN apk --no-cache add libpq
WORKDIR /app

ENV TELEGRAM_TOKEN ${TELEGRAM_TOKEN}
ENV DATABASE_URI ${DATABASE_URI}
ENV HTTP_PROXY ${HTTP_PROXY}
ENV HTTPS_PROXY ${HTTPS_PROXY}

COPY . .

RUN chmod +x main.py
CMD [ "python3", "-m" , "main"]