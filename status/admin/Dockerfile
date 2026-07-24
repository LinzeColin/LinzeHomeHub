FROM python:3.12-slim
RUN pip install --no-cache-dir "PyJWT[crypto]==2.9.0"
WORKDIR /app
COPY app.py /app/app.py
EXPOSE 8080
CMD ["python", "-u", "app.py"]
