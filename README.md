# Akita MeshFS

Akita MeshFS is a distributed, secure, and peer-to-peer encrypted file system designed for the Reticulum network. It aims to provide ZFS-like capabilities in a decentralized environment, optimized for both mobile and desktop use.

## Features

* **End-to-End Encryption:** All data is encrypted before leaving the client, ensuring privacy and security.
* **Distributed Storage:** Data is sharded and distributed across participating Reticulum nodes, providing redundancy and availability.
* **Data Integrity:** Checksums and erasure coding are used to ensure data integrity and recover from corruption.
* **Basic Data Replication:** Data is replicated across multiple nodes for increased redundancy.
* **Basic Data Repair:** Missing data shards can be reconstructed using erasure coding.
* **Basic Directory Structure:** Support for organizing files into directories.
* **Basic Snapshots:** Create point-in-time snapshots of the file system.
* **Basic Quotas:** Implement simple storage quotas.
* **Reticulum Integration:** Leverages Reticulum's P2P networking, addressing, and security features.
* **Simulated Distributed Metadata:** Demonstrates a basic simulation of distributed metadata management.
* **Simulated Reticulum Discovery:** Simulates node discovery within the Reticulum network.

## Getting Started

### Prerequisites

* Python 3.x
* Reticulum
* PyCryptodome (`pip install pycryptodome`)
* reedsolo (`pip install reedsolo`)
* Reticulum Python bindings (`pip install reticulum`)

### Installation

1.  Clone the repository:

    ```bash
    git clone [repository_url]
    cd akita-meshfs
    ```

2.  Install the required Python packages:

    ```bash
    pip install -r requirements.txt
    ```

### Running Akita MeshFS

1.  Start Reticulum.

2.  Run the Akita MeshFS nodes:

    ```bash
    python akita_meshfs.py
    ```

    This will start multiple simulated nodes.

3.  Use the client to interact with the file system:

    ```bash
    python akita_meshfs.py <command>
    ```

    Replace `<command>` with the desired operation (e.g., `write`, `read`, `list`, `delete`).

### Example Usage

* **Write a file:**

    ```bash
    python akita_meshfs.py write:dir1/test.txt:This is some test data.
    ```

* **Read a file:**

    ```bash
    python akita_meshfs.py read:dir1/test.txt
    ```

* **List files:**

    ```bash
    python akita_meshfs.py list:dir1
    ```

* **Delete a file:**

    ```bash
    python akita_meshfs.py delete:dir1/test.txt
    ```

## Development

To contribute to Akita MeshFS, please follow these guidelines:

* Fork the repository.
* Create a new branch for your feature or bug fix.
* Write clear and concise code.
* Add comments and documentation where necessary.
* Test your changes thoroughly.
* Submit a pull request.

## Next Steps

* **Implement a real distributed metadata system with consensus.**
* **Replace simulated Reticulum discovery with actual Reticulum discovery mechanisms.**
* **Implement advanced data replication strategies.**
* **Enhance security with robust key management and access control.**
* **Improve performance with caching and asynchronous operations.**
* **Add a command-line interface and a graphical user interface (GUI).**
* **Develop a mobile application.**
* **Create extensive tests and ensure fault tolerance.**
* **Fully integrate with Reticulum features.**
