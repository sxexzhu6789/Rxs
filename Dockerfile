FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["sh", "-c", "if [ -f Rishh.py ]; then python Rishh.py; elif [ -f rxsv2.py ]; then python rxsv2.py; elif [ -f main.py ]; then python main.py; else python *.py; fi"]
