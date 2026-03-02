FROM locustio/locust:2.32.7

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

# copy examples so users can run them directly
COPY examples/ examples/

EXPOSE 8089

ENTRYPOINT ["locust"]
CMD ["--web-host", "0.0.0.0"]
