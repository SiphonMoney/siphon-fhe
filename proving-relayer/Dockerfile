FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    python3 python3-pip nodejs npm \
    build-essential cmake git \
    && rm -rf /var/lib/apt/lists/*

# Install rapidsnark
RUN git clone https://github.com/iden3/rapidsnark.git /rapidsnark && \
    cd /rapidsnark && \
    git submodule update --init --recursive && \
    mkdir build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j4 && \
    cp prover /usr/local/bin/rapidsnark

WORKDIR /app
COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY . .

EXPOSE 5010
CMD ["python3", "app.py"]
