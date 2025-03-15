import os
import json
import random
import hashlib
import zlib
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
import base64
import reticulum
import time
import threading
import logging
import reedsolo

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Simulated Distributed Metadata Server
distributed_metadata = {}
metadata_lock = threading.Lock()

# Simulated Node Discovery (Dynamic)
node_addresses = ['akita_meshfs_1', 'akita_meshfs_2', 'akita_meshfs_3']

class AkitaMeshFS:
    def __init__(self, container_path, key, reticulum_identity, quota=1024 * 1024 * 1024, data_shards=4, parity_shards=2, replicas=2):
        self.container_path = container_path
        self.key = key
        self.reticulum_identity = reticulum_identity
        self.container_size = 1024 * 1024 * 1024
        self.block_size = 4096
        self.data_shards = data_shards
        self.parity_shards = parity_shards
        self.rs = reedsolo.RSCodec(parity_shards)
        self.quota = quota
        self.replicas = replicas
        self.lock = threading.Lock()
        self.reticulum_link = reticulum.Link.announce(reticulum.Address(self.reticulum_identity), self.handle_reticulum_request)

        if not os.path.exists(self.container_path):
            with open(self.container_path, 'wb') as f:
                f.seek(self.container_size - 1)
                f.write(b'\0')

    def encrypt(self, data):
        compressed_data = zlib.compress(data.encode('utf-8'))
        cipher = AES.new(self.key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(compressed_data)
        nonce = cipher.nonce
        return base64.b64encode(nonce + tag + ciphertext).decode('utf-8')

    def decrypt(self, ciphertext):
        ciphertext = base64.b64decode(ciphertext)
        nonce = ciphertext[:16]
        tag = ciphertext[16:32]
        ciphertext = ciphertext[32:]
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        compressed_data = cipher.decrypt_and_verify(ciphertext, tag)
        return zlib.decompress(compressed_data).decode('utf-8')

    def write_file(self, filename, data):
        encrypted_data = self.encrypt(data)
        shards = [encrypted_data[i:i + self.block_size] for i in range(0, len(encrypted_data), self.block_size)]
        encoded_shards = self.rs.encode(b''.join([s.encode('utf-8') for s in shards]))
        encoded_shards = [encoded_shards[i::len(shards)] for i in range(len(shards))]

        if sum(len(self.get_metadata(f, {}).get('blocks', [])) * self.block_size for f in self.get_metadata()) + len(encoded_shards) * self.block_size * self.replicas > self.quota:
            return False

        available_nodes = self.discover_nodes()
        if len(available_nodes) < self.replicas:
            return False

        allocated_blocks = {}
        for i, shard in enumerate(encoded_shards):
            selected_nodes = random.sample(available_nodes, min(self.replicas, len(available_nodes)))
            allocated_blocks[i] = {}
            for node in selected_nodes:
                with self.lock:
                    free_blocks = self.get_free_blocks(node)
                    if not free_blocks:
                        return False
                    block = free_blocks[0]
                    self.remove_blocks(node, [block])
                    allocated_blocks[i][node] = block

                with open(self.get_node_container_path(node), 'r+b') as f:
                    f.seek(allocated_blocks[i][node] * self.block_size)
                    checksum = hashlib.sha256(shard).hexdigest()
                    f.write(shard + checksum.encode('utf-8'))

        metadata = {'blocks': allocated_blocks, 'size': len(data)}
        self.update_metadata(filename, metadata)
        return True

    def read_file(self, filename):
        metadata = self.get_metadata(filename)
        if not metadata:
            return None

        encoded_shards = [None] * len(metadata['blocks'])
        for shard_index, node_blocks in metadata['blocks'].items():
            for node, block in node_blocks.items():
                try:
                    with open(self.get_node_container_path(node), 'rb') as f:
                        f.seek(block * self.block_size)
                        shard_with_checksum = f.read(self.block_size + 64)
                        shard = shard_with_checksum[:-64]
                        checksum = shard_with_checksum[-64:].decode('utf-8')
                        if hashlib.sha256(shard).hexdigest() != checksum:
                            logging.error(f"Checksum mismatch for block {block} on node {node}")
                            continue
                        encoded_shards[int(shard_index)] = shard
                        break
                except FileNotFoundError:
                    logging.error(f"Node {node} container not found.")
                    continue

        if None in encoded_shards:
            logging.warning("Missing shards detected. Attempting repair.")
            try:
                encoded_shards = self.rs.decode(b''.join(encoded_shards))
                encoded_shards = [encoded_shards[i:i+self.block_size] for i in range(0, len(encoded_shards), self.block_size)]

            except Exception as e:
                logging.error(f"Repair failed: {e}")
                return None

        try:
            encrypted_data = b''.join(encoded_shards).decode('utf-8')
            decrypted_data = self.decrypt(encrypted_data)
            return decrypted_data
        except Exception as e:
            logging.error(f"Decryption or erasure coding error: {e}")
            return None

    def delete_file(self, filename):
        metadata = self.get_metadata(filename)
        if not metadata:
            return False

        for shard_index, node_blocks in metadata['blocks'].items():
            for node, block in node_blocks.items():
                self.add_blocks(node, [block])
        self.remove_metadata(filename)
        return True

    def list_files(self, directory=''):
        files = []
        for filename in self.get_metadata():
            if filename.startswith(directory):
                files.append(filename)
        return files

    def get_free_blocks(self, node):
        used_blocks = set()
        node_metadata = self.get_node_metadata(node)
        for file_info in node_metadata.values():
            if 'blocks' in file_info:
                used_blocks.update(file_info['blocks'])
        return sorted(list(set(range(self.container_size // self.block_size)) - used_blocks))

    def add_blocks(self, node, blocks):
        with self.lock:
            free_blocks = self.get_free_blocks(node)
            free_blocks.extend(blocks)
            free_blocks.sort()

    def remove_blocks(self, node, blocks):
        with self.lock:
            free_blocks = self.get_free_blocks(node)
            for block in blocks:
                free_blocks.remove(block)

    def get_metadata(self, filename=None):
        with metadata_lock:
            if filename:
                return distributed_metadata.get(filename, None)
            else
          return distributed_metadata

    def update_metadata(self, filename, metadata):
        with metadata_lock:
            distributed_metadata[filename] = metadata

    def remove_metadata(self, filename):
        with metadata_lock:
            if filename in distributed_metadata:
                del distributed_metadata[filename]

    def get_node_metadata(self, node):
        node_metadata = {}
        for filename, metadata in self.get_metadata().items():
            if 'blocks' in metadata:
                for shard_index, node_blocks in metadata['blocks'].items():
                    if node in node_blocks:
                        if node not in node_metadata:
                            node_metadata[node] = {}
                        if filename not in node_metadata[node]:
                            node_metadata[node][filename] = {'blocks': []}
                        node_metadata[node][filename]['blocks'].append(node_blocks[node])
        return node_metadata.get(node, {})

    def get_node_container_path(self, node):
        return f'akita_meshfs_{node}_container.bin'

    def discover_nodes(self):
        return node_addresses

    def handle_reticulum_request(self, link, packet):
        try:
            request = packet.content.decode('utf-8').split(':', 1)
            command = request[0]
            if command == 'write':
                filename, data = request[1].split(':', 1)
                success = self.write_file(filename, data)
                link.send(('write_response:' + str(success)).encode('utf-8'))
            elif command == 'read':
                filename = request[1]
                data = self.read_file(filename)
                if data is not None:
                    link.send(('read_response:' + data).encode('utf-8'))
                else:
                    link.send(('read_response:None').encode('utf-8'))
            elif command == 'delete':
                filename = request[1]
                success = self.delete_file(filename)
                link.send(('delete_response:' + str(success)).encode('utf-8'))
            elif command == 'list':
                directory = request[1] if len(request) > 1 else ''
                files = self.list_files(directory)
                link.send(('list_response:' + json.dumps(files)).encode('utf-8'))
            else:
                link.send('unknown_command'.encode('utf-8'))
        except Exception as e:
            logging.error(f"Error handling request: {e}")
            link.send(('error:'+str(e)).encode('utf-8'))

def run_akita_meshfs(container_path, key, reticulum_identity):
    akita_meshfs = AkitaMeshFS(container_path, key, reticulum_identity)
    logging.info(f"Akita MeshFS '{reticulum_identity}' started. Listening for requests...")
    while True:
        time.sleep(1)

def akita_meshfs_client(target_identity, command):
    destination = reticulum.Address(target_identity)
    reticulum.Link.establish(destination)
    reticulum.Link.wait_for_link(destination)
    reticulum.Link.send(destination, command.encode('utf-8'))
    packet = reticulum.Packet.receive()
    if packet:
        try:
            return packet.content.decode('utf-8')
        except UnicodeDecodeError:
            return packet.content
    return None

if __name__ == "__main__":
    key = get_random_bytes(32)
    node_identities = ['akita_meshfs_1', 'akita_meshfs_2', 'akita_meshfs_3']

    container_threads = []
    for identity in node_identities:
        container_path = f'akita_meshfs_{identity}_container.bin'
        container_thread = threading.Thread(target=run_akita_meshfs, args=(container_path, key, identity))
        container_thread.daemon = True
        container_threads.append(container_thread)
        container_thread.start()

    time.sleep(2)

    print(akita_meshfs_client(node_identities[0], 'write:dir1/test.txt:This is some test data.'))
    print(akita_meshfs_client(node_identities[0], 'read:dir1/test.txt'))
    print(akita_meshfs_client(node_identities[0], 'list:dir1'))
    print(akita_meshfs_client(node_identities[0], 'list:'))
    print(akita_meshfs_client(node_identities[0], 'delete:dir1/test.txt'))
    print(akita_meshfs_client(node_identities[0], 'read:dir1/test.txt'))
    print(akita_meshfs_client(node_identities[0], 'list:dir1'))
    print(akita_meshfs_client(node_identities[0], 'list:'))
