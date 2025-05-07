# Akita MeshFS

**Akita MeshFS is a conceptual prototype of a distributed, secure, and peer-to-peer encrypted file system designed for the Reticulum network. It aims to demonstrate ZFS-like capabilities (such as snapshots and data integrity) in a decentralized environment, optimized for both mobile and desktop use scenarios.**

**Current Status:** This project is currently a **simulation and proof-of-concept**. Key distributed components like metadata management and node discovery are simulated within a single Python process using threads. It serves as a foundation for future development into a truly distributed system.

## Features (Simulated)

* **End-to-End Encryption:** All file data is encrypted client-side using AES-GCM before being processed for storage, ensuring privacy.
* **Distributed Storage (Sharding):** Data is broken into shards, which are then (conceptually) distributed across participating simulated Reticulum nodes.
* **Data Integrity:**
    * **Checksums:** SHA256 checksums are used for each data shard to verify integrity upon retrieval.
    * **Erasure Coding:** Reed-Solomon erasure coding is employed to allow reconstruction of data even if some shards are lost or corrupted.
* **Data Replication:** Each shard is replicated across multiple simulated nodes for redundancy.
* **Data Repair:** Missing or corrupted data shards can be reconstructed using the stored parity shards from erasure coding.
* **Directory Structure:** Basic support for organizing files into directory-like paths (e.g., `docs/notes.txt`).
* **Basic Snapshots (File-Level):**
    * Point-in-time snapshots can be created for individual files.
    * Utilizes a (simulated) block reference counting mechanism to ensure data blocks are not freed if referenced by a file or any snapshot.
* **Basic Quotas:** A simple global storage quota is checked (simulated).
* **Reticiculum Integration (Simulated):**
    * Leverages the Reticulum Python library for simulated peer-to-peer communication between threads representing nodes.
    * Nodes "announce" themselves, and a client can send requests to them.
* **Simulated Distributed Metadata:** File and snapshot metadata is managed in a centralized Python dictionary, protected by locks, simulating a distributed metadata store.
* **Simulated Node Discovery:** The list of participating nodes is currently hardcoded for the simulation.
* **Simulated Block Reference Counting:** A global dictionary tracks references to data blocks to support snapshot functionality.

## How It Works (Simulation Overview)

1.  **Simulated Nodes:** Multiple Akita MeshFS nodes are launched as separate threads within a single Python process. Each "node" manages its own virtual storage container (a local file).
2.  **Data Processing Pipeline:**
    * **Write:** When a file is written, its content is encrypted, then processed with Reed-Solomon erasure coding to produce data and parity shards. Each shard is replicated and written (along with a checksum) to blocks in the container files of several simulated nodes.
    * **Read:** To read, shards are retrieved from node containers, checksums are verified, and erasure coding is used to reconstruct data if necessary. The data is then decrypted.
3.  **Metadata & State:** All metadata (file info, shard locations, snapshot details) and block reference counts are stored in global Python dictionaries, simulating a shared, consistent view for all nodes in this single-process environment.
4.  **Communication:** The Reticulum Python library facilitates communication between the client logic and the "node" threads, simulating network requests and responses.

## Prerequisites

* Python 3.7+
* Reticulum Network Stack (`rns` Python library)
* PyCryptodome (`pycryptodome` Python library)
* reedsolo (`reedsolo` Python library)

## Getting Started

1.  **Clone the repository (example):**
    ```bash
    git clone [https://github.com/AkitaEngineering/akita-meshfs.git]
    cd akita-meshfs
    ```

2.  **Create a `requirements.txt` file with the following content:**
    ```txt
    rns
    pycryptodome
    reedsolo
    ```

3.  **Install the required Python packages:**
    ```bash
    pip install -r requirements.txt
    ```

## Running Akita MeshFS (The Simulation)

1.  **Ensure Reticulum is usable:** For the simulation to run, the Reticulum library just needs to be installed. No external Reticulum daemons or specific network interface configurations are strictly necessary for this single-process simulation, as Reticulum will operate in an internal mode.

2.  **Run the main script:**
    ```bash
    python akita_meshfs.py
    ```
    (Assuming the main Python file is named `akita_meshfs.py`)

    This will:
    * Initialize a global Reticulum instance.
    * Start multiple simulated Akita MeshFS node threads. Each node will create its own storage container file (e.g., `akita_node_storage/akita_meshfs_container_akitaNodeAlpha.bin`).
    * After a brief pause for nodes to initialize and "announce" themselves (simulated), the script will execute a series of client demo operations, printing output to the console.

## Example Client Commands (Used in the Demo)

The client communicates with a target node using commands in the format `<command_name>:<param1>:<param2>...`.
The demo in `akita_meshfs.py` showcases:

* **Write a file:**
    `write:docs/notes.txt:Hello Akita World! This is a test file.`
* **Read a file:**
    `read:docs/notes.txt`
* **List items (files and snapshots):**
    `list:docs/` (lists items starting with "docs/")
    `list:` (lists all items)
    `list:docs/:file` (lists only files starting with "docs/")
* **Create a snapshot:**
    `snapshot_create:docs/notes.txt:docs/notes.txt@snap1`
* **Delete an item (file or snapshot):**
    `delete:docs/notes.txt`
    `delete:docs/notes.txt@snap1`

## Development

If you wish to contribute to Akita MeshFS (once it's in a more collaborative environment):

1.  Fork the repository.
2.  Create a new branch for your feature or bug fix (`git checkout -b feature/your-feature-name`).
3.  Write clear, concise, and well-commented code.
4.  Add tests for your changes.
5.  Ensure your changes pass all existing tests.
6.  Submit a pull request with a clear description of your changes.

## Project Status & Future Work

Akita MeshFS is currently a **proof-of-concept and simulation**. It successfully demonstrates core ideas but requires significant development to become a truly distributed and production-ready system.

Key areas for future development include:

* **Real Distributed Metadata:** Implement a robust distributed metadata system (e.g., using a DHT like Kademlia over Reticulum, or a consensus algorithm) to replace the current centralized Python dictionary.
* **True Peer Discovery:** Integrate actual Reticulum discovery mechanisms instead of the hardcoded node list.
* **Decentralized Block Reference Counting:** Distribute the block reference counting mechanism along with the metadata.
* **Advanced Data Replication & Repair:** Implement configurable strategies, proactive repair, and intelligent replica placement.
* **Enhanced Security:**
    * Robust key management and distribution.
    * File/directory access control lists (ACLs) tied to Reticulum identities.
    * Mechanisms to handle malicious or non-cooperative nodes.
* **Performance Optimizations:** Caching, asynchronous operations, efficient data structures.
* **User Interfaces:**
    * A comprehensive Command-Line Interface (CLI).
    * A FUSE (Filesystem in Userspace) adapter to allow mounting Akita MeshFS as a local filesystem.
    * Graphical User Interface (GUI) and mobile applications.
* **Fault Tolerance & Scalability:** Design and test for high availability, data durability under various failure scenarios, and scalability to many nodes and large datasets.
* **Comprehensive Testing:** Extensive unit, integration, and stress testing in realistic distributed environments.
