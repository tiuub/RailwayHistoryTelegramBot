FROM python:3.9-slim-buster

WORKDIR /app

ENV TELEGRAM_TOKEN ${TELEGRAM_TOKEN}
ENV DATABASE_URI ${DATABASE_URI}
ENV HTTP_PROXY ${HTTP_PROXY}
ENV HTTPS_PROXY ${HTTPS_PROXY}

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

RUN chmod +x main.py
CMD [ "python3", "-m" , "main"]