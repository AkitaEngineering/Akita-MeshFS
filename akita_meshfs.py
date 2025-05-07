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
from copy import deepcopy # For snapshot metadata

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Simulated Global State (Centralized for Simulation) ---
# In a real distributed system, these would be managed by complex distributed algorithms.

# Simulated Distributed Metadata Server
distributed_metadata = {}
metadata_lock = threading.Lock() # Protects distributed_metadata

# Simulated Node Discovery (Hardcoded for simulation)
node_addresses = ['akitaNodeAlpha', 'akitaNodeBravo', 'akitaNodeCharlie']

# Simulated Block Reference Counts: {'node_id': {block_num_on_node: count}}
# Tracks how many metadata entries (files/snapshots) reference a physical block.
block_reference_counts = {}
ref_count_lock = threading.Lock() # Protects block_reference_counts

# --- End Simulated Global State ---

class AkitaMeshFS:
    def __init__(self, container_base_path, key, reticulum_identity_str, quota=1024 * 1024 * 1024, data_shards=4, parity_shards=2, replicas=2):
        self.container_base_path = container_base_path # Base path for node containers
        self.key = key
        self.reticulum_identity_str = reticulum_identity_str # e.g., "akitaNodeAlpha"
        self.container_size_bytes = 1024 * 1024 * 1024 # Total size of each node's virtual container
        self.block_size_bytes = 4096  # Size of a single block in a node's container file.
        self.data_shards = data_shards
        self.parity_shards = parity_shards
        self.rs_codec = reedsolo.RSCodec(self.parity_shards)
        self.storage_quota_bytes = quota
        self.num_replicas = replicas
        self.instance_lock = threading.Lock() # General lock for instance-specific operations if needed

        try:
            r_address = reticulum.Address(self.reticulum_identity_str)
            # Pass the reticulum_identity_str as app_data for potential use by clients or other nodes.
            self.reticulum_link = reticulum.Link.announce(
                r_address,
                self._handle_reticulum_request, # Note: renamed to avoid confusion with global RNS object
                app_data=self.reticulum_identity_str.encode("utf-8")
            )
            logging.info(f"Node '{self.reticulum_identity_str}' announced on Reticulum address '{r_address.human_readable()}'.")
        except Exception as e:
            logging.error(f"Node '{self.reticulum_identity_str}': Failed to announce on Reticulum: {e}")
            self.reticulum_link = None

        node_specific_container_path = self._get_node_container_path(self.reticulum_identity_str)
        if not os.path.exists(node_specific_container_path):
            try:
                with open(node_specific_container_path, 'wb') as f:
                    f.seek(self.container_size_bytes - 1)
                    f.write(b'\0')
                logging.info(f"Node '{self.reticulum_identity_str}': Created storage container at '{node_specific_container_path}'.")
            except Exception as e:
                logging.error(f"Node '{self.reticulum_identity_str}': Failed to create container: {e}")

        # Initialize ref_count for this node if not present
        with ref_count_lock:
            if self.reticulum_identity_str not in block_reference_counts:
                block_reference_counts[self.reticulum_identity_str] = {}


    def _encrypt(self, data_str):
        compressed_data = zlib.compress(data_str.encode('utf-8'))
        cipher = AES.new(self.key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(compressed_data)
        nonce = cipher.nonce
        return base64.b64encode(nonce + tag + ciphertext).decode('utf-8')

    def _decrypt(self, ciphertext_b64_str):
        decoded_data = base64.b64decode(ciphertext_b64_str.encode('utf-8'))
        nonce, tag, ciphertext = decoded_data[:16], decoded_data[16:32], decoded_data[32:]
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)
        try:
            compressed_data = cipher.decrypt_and_verify(ciphertext, tag)
            return zlib.decompress(compressed_data).decode('utf-8')
        except (ValueError, zlib.error, UnicodeDecodeError) as e:
            logging.error(f"Node '{self.reticulum_identity_str}': Decryption/decompression failed: {e}")
            raise Exception(f"Decryption/decompression failed: {e}") from e

    def _update_block_ref_count(self, node_id, block_num, delta):
        with ref_count_lock:
            if node_id not in block_reference_counts:
                block_reference_counts[node_id] = {}
            block_ref = block_reference_counts[node_id]
            block_ref[block_num] = block_ref.get(block_num, 0) + delta
            if block_ref[block_num] <= 0: # Clean up if count is zero or less
                del block_ref[block_num]

    def write_file(self, filepath_str, data_str, item_type="file", source_filepath_for_snapshot=None):
        # Lock to ensure atomic file write operation including metadata and ref_counts
        with self.instance_lock, metadata_lock, ref_count_lock:
            if self._get_metadata(filepath_str) is not None:
                logging.warning(f"Node '{self.reticulum_identity_str}': Item '{filepath_str}' already exists. Write failed.")
                return False

            encrypted_data_str = self._encrypt(data_str)
            payload_bytes = encrypted_data_str.encode('utf-8')
            original_payload_len = len(payload_bytes)
            rs_input_chunk_len = (original_payload_len + self.data_shards - 1) // self.data_shards

            if rs_input_chunk_len + 64 > self.block_size_bytes: # 64 for checksum
                logging.error(f"Node '{self.reticulum_identity_str}': File '{filepath_str}' too large for config. "
                              f"RS chunk ({rs_input_chunk_len}) + checksum (64) > block size ({self.block_size_bytes}).")
                return False

            rs_input_chunks = [payload_bytes[i * rs_input_chunk_len : min((i + 1) * rs_input_chunk_len, original_payload_len)].ljust(rs_input_chunk_len, b'\0')
                               for i in range(self.data_shards)]
            try:
                all_storage_shards = self.rs_codec.encode(rs_input_chunks)
            except Exception as e:
                logging.error(f"Node '{self.reticulum_identity_str}': Reed-Solomon encoding failed for '{filepath_str}': {e}")
                return False

            # Quota check
            bytes_for_this_file = len(all_storage_shards) * self.block_size_bytes * self.num_replicas
            current_usage = sum(
                f_meta.get('num_storage_shards', 0) * self.block_size_bytes * f_meta.get('replicas', 0)
                for f_meta in distributed_metadata.values()
            ) # Simplified quota based on allocated blocks rather than exact rs_chunk_len
            if current_usage + bytes_for_this_file > self.storage_quota_bytes:
                logging.warning(f"Node '{self.reticulum_identity_str}': Quota exceeded for '{filepath_str}'.")
                return False

            available_nodes = self._discover_nodes()
            if len(available_nodes) < self.num_replicas:
                logging.warning(f"Node '{self.reticulum_identity_str}': Not enough nodes ({len(available_nodes)}) for {self.num_replicas} replicas for '{filepath_str}'.")
                return False

            allocated_blocks_metadata = {} # {shard_idx_str: {node_id: block_num_on_node}}
            temp_allocated_blocks_for_rollback = [] # [(node_id, block_num)]

            try:
                for i, shard_data_bytes in enumerate(all_storage_shards):
                    if len(available_nodes) < self.num_replicas: return False # Should have been caught earlier
                    selected_nodes = random.sample(available_nodes, self.num_replicas)
                    allocated_blocks_metadata[str(i)] = {}

                    for node_id in selected_nodes:
                        free_blocks_on_node = self._get_free_blocks(node_id)
                        if not free_blocks_on_node:
                            logging.warning(f"Node '{self.reticulum_identity_str}': Node {node_id} has no free blocks for shard {i} of '{filepath_str}'.")
                            raise Exception("Insufficient free blocks on a selected node.") # Trigger rollback

                        block_num_on_node = free_blocks_on_node[0]
                        checksum = hashlib.sha256(shard_data_bytes).hexdigest()
                        data_to_store = (shard_data_bytes + checksum.encode('utf-8')).ljust(self.block_size_bytes, b'\0')

                        with open(self._get_node_container_path(node_id), 'r+b') as f_node:
                            f_node.seek(block_num_on_node * self.block_size_bytes)
                            f_node.write(data_to_store)

                        allocated_blocks_metadata[str(i)][node_id] = block_num_on_node
                        self._update_block_ref_count(node_id, block_num_on_node, 1) # Increment ref count
                        temp_allocated_blocks_for_rollback.append((node_id, block_num_on_node))

                file_meta = {
                    'type': item_type,
                    'filepath': filepath_str, # For snapshots, this is the snapshot path
                    'blocks': allocated_blocks_metadata,
                    'original_size_bytes': len(data_str.encode('utf-8')),
                    'original_payload_len': original_payload_len,
                    'rs_input_chunk_len': rs_input_chunk_len,
                    'num_data_shards': self.data_shards,
                    'num_parity_shards': self.parity_shards,
                    'num_storage_shards': len(all_storage_shards),
                    'replicas': self.num_replicas,
                    'timestamp': time.time()
                }
                if item_type == "snapshot" and source_filepath_for_snapshot:
                    file_meta['source_filepath'] = source_filepath_for_snapshot

                self._update_metadata(filepath_str, file_meta)
                logging.info(f"Node '{self.reticulum_identity_str}': Item '{filepath_str}' (type: {item_type}) written successfully.")
                return True
            except Exception as e:
                logging.error(f"Node '{self.reticulum_identity_str}': Error during write of '{filepath_str}': {e}. Rolling back.")
                # Rollback: decrement ref counts for allocated blocks
                for node_id, block_num in temp_allocated_blocks_for_rollback:
                    self._update_block_ref_count(node_id, block_num, -1)
                # Data in container files is overwritten garbage now, but blocks are marked free by ref count.
                return False


    def read_file(self, filepath_str):
        with metadata_lock: # Ensure metadata isn't changed during read
            file_metadata = deepcopy(self._get_metadata(filepath_str)) # Work with a copy

        if not file_metadata:
            logging.warning(f"Node '{self.reticulum_identity_str}': Item '{filepath_str}' not found.")
            return None

        original_payload_len = file_metadata['original_payload_len']
        rs_input_chunk_len = file_metadata['rs_input_chunk_len']
        num_total_shards = file_metadata['num_storage_shards']
        num_data_shards_for_file = file_metadata['num_data_shards']
        # Create a codec instance based on file's metadata for correct parity count
        file_rs_codec = reedsolo.RSCodec(file_metadata['num_parity_shards'])


        retrieved_shards_for_rs = [None] * num_total_shards
        for shard_idx_str, node_block_map in file_metadata['blocks'].items():
            shard_idx = int(shard_idx_str)
            if shard_idx >= num_total_shards: continue # Should not happen

            for node_id, block_num_on_node in node_block_map.items():
                try:
                    with open(self._get_node_container_path(node_id), 'rb') as f_node:
                        f_node.seek(block_num_on_node * self.block_size_bytes)
                        block_content = f_node.read(self.block_size_bytes)
                        
                        # Expected length of data + checksum
                        shard_with_checksum = block_content[:rs_input_chunk_len + 64]
                        shard_data_bytes = shard_with_checksum[:-64]
                        checksum_stored = shard_with_checksum[-64:].decode('utf-8', errors='ignore')
                        
                        if hashlib.sha256(shard_data_bytes).hexdigest() == checksum_stored:
                            retrieved_shards_for_rs[shard_idx] = shard_data_bytes
                            break # Got a good copy of this shard
                except Exception as e:
                    logging.warning(f"Node '{self.reticulum_identity_str}': Error reading shard {shard_idx} of '{filepath_str}' from node {node_id}, block {block_num_on_node}: {e}")
        
        try:
            # Pass all k+m shards (or Nones) to decode
            repaired_data_chunks = file_rs_codec.decode(retrieved_shards_for_rs)
            # decode returns only the k data shards
            if len(repaired_data_chunks) != num_data_shards_for_file:
                 raise reedsolo.ReedSolomonError(f"Not enough data shards returned after decode: got {len(repaired_data_chunks)}, expected {num_data_shards_for_file}")

        except reedsolo.ReedSolomonError as e:
            logging.error(f"Node '{self.reticulum_identity_str}': Reed-Solomon decoding failed for '{filepath_str}': {e}.")
            return None

        payload_bytes_padded = b''.join(repaired_data_chunks)
        payload_bytes = payload_bytes_padded[:original_payload_len] # Trim padding

        try:
            encrypted_data_str = payload_bytes.decode('utf-8')
            decrypted_data = self._decrypt(encrypted_data_str)
            logging.info(f"Node '{self.reticulum_identity_str}': Item '{filepath_str}' read and decrypted successfully.")
            return decrypted_data
        except Exception as e: # Catches decryption or decode errors
            logging.error(f"Node '{self.reticulum_identity_str}': Final processing (decryption/decode) failed for '{filepath_str}': {e}")
            return None

    def delete_item(self, filepath_str): # Deletes a file or a snapshot
        with metadata_lock, ref_count_lock: # Ensure atomicity
            file_metadata = self._get_metadata(filepath_str)
            if not file_metadata:
                logging.warning(f"Node '{self.reticulum_identity_str}': Item '{filepath_str}' not found for deletion.")
                return False

            # Decrement reference counts for all blocks this item was using
            for shard_idx_str, node_block_map in file_metadata['blocks'].items():
                for node_id, block_num_on_node in node_block_map.items():
                    self._update_block_ref_count(node_id, block_num_on_node, -1)

            self._remove_metadata(filepath_str) # Remove the metadata entry
            logging.info(f"Node '{self.reticulum_identity_str}': Item '{filepath_str}' (type: {file_metadata.get('type', 'unknown')}) deleted.")
            return True

    def create_snapshot(self, source_filepath_str, snapshot_filepath_str):
        with metadata_lock, ref_count_lock: # Ensure atomicity
            source_metadata = self._get_metadata(source_filepath_str)
            if not source_metadata: # Snapshots can be from files or other snapshots (if allowed, currently from files)
                logging.warning(f"Node '{self.reticulum_identity_str}': Source item '{source_filepath_str}' not found for snapshot.")
                return False
            # if source_metadata.get('type') != 'file': # Or allow snapshots of snapshots
            #     logging.warning(f"Node '{self.reticulum_identity_str}': Source '{source_filepath_str}' is not a file, cannot snapshot.")
            #     return False
            if self._get_metadata(snapshot_filepath_str) is not None:
                logging.warning(f"Node '{self.reticulum_identity_str}': Snapshot name '{snapshot_filepath_str}' already exists.")
                return False

            snapshot_metadata = deepcopy(source_metadata) # Key step: copy metadata
            snapshot_metadata['type'] = 'snapshot'
            snapshot_metadata['filepath'] = snapshot_filepath_str # The snapshot's own identifier
            snapshot_metadata['source_filepath'] = source_filepath_str # Original source
            snapshot_metadata['timestamp'] = time.time()

            # Increment reference counts for all blocks this new snapshot now points to
            for shard_idx_str, node_block_map in snapshot_metadata['blocks'].items():
                for node_id, block_num_on_node in node_block_map.items():
                    self._update_block_ref_count(node_id, block_num_on_node, 1)
            
            self._update_metadata(snapshot_filepath_str, snapshot_metadata)
            logging.info(f"Node '{self.reticulum_identity_str}': Snapshot '{snapshot_filepath_str}' created from '{source_filepath_str}'.")
            return True

    def list_items(self, directory_or_prefix_str='', item_type_filter=None):
        with metadata_lock:
            items = []
            # Iterate over a copy in case of concurrent modification (though lock helps)
            for item_name, meta in list(distributed_metadata.items()):
                if item_name.startswith(directory_or_prefix_str):
                    if item_type_filter and meta.get('type') != item_type_filter:
                        continue
                    items.append({
                        "name": item_name, # This is the full path/identifier
                        "type": meta.get("type", "unknown"),
                        "size": meta.get("original_size_bytes", 0),
                        "timestamp": meta.get("timestamp", 0),
                        "source_filepath": meta.get("source_filepath", None) if meta.get("type") == "snapshot" else None
                    })
            return sorted(items, key=lambda x: x['name'])


    def _get_free_blocks(self, node_id_str):
        # Free blocks are those not present in block_reference_counts for this node
        # or with a count of 0 (though _update_block_ref_count should remove entries with count <= 0).
        with ref_count_lock: # Ensure consistent view of ref counts
            used_blocks_on_node = set(block_reference_counts.get(node_id_str, {}).keys())
        
        total_blocks_in_container = self.container_size_bytes // self.block_size_bytes
        all_block_indices = set(range(total_blocks_in_container))
        free_block_indices = sorted(list(all_block_indices - used_blocks_on_node))
        return free_block_indices

    def _get_metadata(self, filepath_str=None):
        # Assumes metadata_lock is acquired by the calling public method if modification context
        if filepath_str:
            return distributed_metadata.get(filepath_str) # Returns None if not found
        return distributed_metadata # For iteration, caller should handle locking or copy

    def _update_metadata(self, filepath_str, metadata_item):
        # Assumes metadata_lock is acquired by the calling public method
        distributed_metadata[filepath_str] = metadata_item

    def _remove_metadata(self, filepath_str):
        # Assumes metadata_lock is acquired by the calling public method
        if filepath_str in distributed_metadata:
            del distributed_metadata[filepath_str]

    def _get_node_container_path(self, node_id_str):
        # Sanitize node_id_str for use in filenames
        safe_node_id = "".join(c if c.isalnum() or c in ['-', '_'] else '_' for c in node_id_str)
        return os.path.join(self.container_base_path, f'akita_meshfs_container_{safe_node_id}.bin')

    def _discover_nodes(self):
        # Simulated: In a real system, this would use Reticulum's discovery.
        return list(node_addresses) # Return a copy to prevent modification of global

    def _handle_reticulum_request(self, link_obj, data_bytes, packet_obj):
        # link_obj: The server's link instance that received the data.
        # data_bytes: The raw payload.
        # packet_obj: The Reticulum Packet object.
        if not data_bytes:
            logging.warning(f"Node '{self.reticulum_identity_str}': Received empty data.")
            return

        response_str = "error:Unknown error" # Default response
        request_str_for_logging = "raw_bytes"
        try:
            request_str = data_bytes.decode('utf-8')
            request_str_for_logging = request_str # For logging in case of error
            parts = request_str.split(':', 2) # command:path_or_source:data_or_snapname
            command = parts[0]
            
            logging.debug(f"Node '{self.reticulum_identity_str}': Received command '{request_str}' from source hash {packet_obj.source_destination_hash.hex()[:10] if packet_obj.source_destination_hash else 'UnknownSource'}")

            if command == 'write':
                if len(parts) == 3:
                    filepath, content = parts[1], parts[2]
                    success = self.write_file(filepath, content) # Defaults to item_type="file"
                    response_str = f'write_response:{str(success)}'
                else: response_str = 'error:Invalid write format. Use write:filepath:content'
            elif command == 'read':
                if len(parts) >= 2:
                    filepath = parts[1]
                    content = self.read_file(filepath)
                    response_str = f'read_response:{content if content is not None else "None"}' # Explicit "None" string
                else: response_str = 'error:Invalid read format. Use read:filepath'
            elif command == 'delete': # Deletes files or snapshots
                if len(parts) >= 2:
                    filepath = parts[1]
                    success = self.delete_item(filepath)
                    response_str = f'delete_response:{str(success)}'
                else: response_str = 'error:Invalid delete format. Use delete:filepath'
            elif command == 'list':
                prefix = parts[1] if len(parts) > 1 and parts[1] else '' # Handle empty prefix part like "list::file"
                item_type = parts[2] if len(parts) > 2 and parts[2] else None # Optional type filter: file, snapshot
                items = self.list_items(prefix, item_type_filter=item_type)
                response_str = f'list_response:{json.dumps(items)}'
            elif command == 'snapshot_create':
                if len(parts) == 3:
                    source_path, snap_path = parts[1], parts[2]
                    success = self.create_snapshot(source_path, snap_path)
                    response_str = f'snapshot_create_response:{str(success)}' # Changed response prefix
                else: response_str = 'error:Invalid snapshot_create format. Use snapshot_create:source_path:snapshot_path'
            else:
                response_str = 'error:unknown_command'
        except Exception as e:
            logging.exception(f"Node '{self.reticulum_identity_str}': Unhandled error processing request '{request_str_for_logging}': {e}")
            response_str = f'error:Internal server error - Unhandled exception: {str(e)}'
        
        try:
            if packet_obj and hasattr(packet_obj, 'reply') and callable(packet_obj.reply):
                packet_obj.reply(response_str.encode('utf-8'))
            # Fallback via link_obj is generally not how replies are structured with packet.reply being preferred.
            # If packet.reply() is not working, it usually indicates a more fundamental issue with the link or packet context.
            else:
                logging.error(f"Node '{self.reticulum_identity_str}': Cannot send response for request (no packet.reply or packet_obj missing).")
        except Exception as e:
            logging.error(f"Node '{self.reticulum_identity_str}': Exception while sending response: {e}")


# --- Main Application Logic & Client ---
RNS_INSTANCE = None # Global Reticulum instance for the process

def run_akita_node_thread(container_base_path, key, node_identity_str):
    global RNS_INSTANCE
    if RNS_INSTANCE is None: # Should be initialized in main, but defensive
        RNS_INSTANCE = reticulum.Reticulum() # Use existing or create new default
    
    akita_node = AkitaMeshFS(container_base_path, key, node_identity_str)
    try:
        while True: # Keep thread alive for Reticulum callbacks which are event-driven
            time.sleep(3600) # Sleep for a long time; actual work is in Reticulum's threads
    except KeyboardInterrupt:
        logging.info(f"Node '{node_identity_str}' thread interrupted by user.")
    finally:
        if akita_node.reticulum_link and hasattr(akita_node.reticulum_link, 'teardown'):
            try:
                akita_node.reticulum_link.teardown()
                logging.info(f"Node '{node_identity_str}' Reticulum link torn down.")
            except Exception as e:
                logging.error(f"Node '{node_identity_str}' error tearing down link: {e}")
        logging.info(f"Node '{node_identity_str}' thread finished.")


def akita_client_request(target_node_app_name_str, command_str, timeout_s=20.0): # Increased timeout
    global RNS_INSTANCE
    if RNS_INSTANCE is None:
        logging.error("Client: Reticulum (RNS_INSTANCE) not initialized!")
        return "error:Client RNS not initialized"

    response_data = f"error:Timeout waiting for response after {timeout_s}s (initial)" # Default if timeout
    response_event = threading.Event()
    # Use a list to pass mutable response_data into callback scope effectively
    response_holder = [response_data]


    def client_callback(link_obj, data_bytes, packet_obj):
        try:
            response_holder[0] = data_bytes.decode('utf-8')
        except Exception as e:
            response_holder[0] = f"error:Client callback decode error - {e}"
        finally:
            response_event.set()
            # Link teardown should happen after event is processed or in the main client logic
            # to avoid race conditions if callback executes fast.

    ephemeral_link = None # Define for finally block
    try:
        server_address = reticulum.Address(target_node_app_name_str)
        ephemeral_link = reticulum.Link(server_address) # This creates an outbound link
        ephemeral_link.set_callback(client_callback) # For the reply
        
        logging.debug(f"Client: Sending '{command_str}' to '{target_node_app_name_str}' via link {ephemeral_link.hash[:6]}...")
        ephemeral_link.send(command_str.encode('utf-8'))

        if response_event.wait(timeout=timeout_s):
            # response_holder[0] has the value set by callback
            pass # Value is in response_holder[0]
        else:
            logging.warning(f"Client: Timeout waiting for response from '{target_node_app_name_str}' for command '{command_str}'.")
            # response_holder[0] retains its default timeout message
        
        return response_holder[0]

    except Exception as e:
        logging.exception(f"Client: Error during request to '{target_node_app_name_str}': {e}")
        return f"error:Client exception - {e}"
    finally:
        if ephemeral_link and hasattr(ephemeral_link, 'teardown'):
            try:
                # Cancel callback before teardown to avoid issues if callback is still pending or running
                if hasattr(ephemeral_link, 'cancel_callback') and callable(ephemeral_link.cancel_callback):
                    ephemeral_link.cancel_callback()
                ephemeral_link.teardown()
            except Exception as e_td:
                logging.error(f"Client: Error tearing down ephemeral link: {e_td}")


if __name__ == "__main__":
    # Initialize Reticulum once for the whole application.
    # Lowering Reticulum's internal loglevel to avoid flooding console from RNS itself.
    # Your application logs (INFO, DEBUG) are separate.
    RNS_INSTANCE = reticulum.Reticulum(loglevel=logging.WARNING) 
    logging.info(f"Reticulum Mobile Stack {reticulum.VERSION} initialized. Node Identity: {RNS_INSTANCE.identity().hash[:10]}...")

    sim_base_storage_path = "./akita_node_storage"
    if not os.path.exists(sim_base_storage_path):
        os.makedirs(sim_base_storage_path, exist_ok=True)
    
    # Clean up old container files and in-memory state for a fresh run
    for item in os.listdir(sim_base_storage_path):
        if item.startswith("akita_meshfs_container_") and item.endswith(".bin"):
            try:
                os.remove(os.path.join(sim_base_storage_path, item))
                logging.info(f"Removed old container: {item}")
            except Exception as e:
                logging.warning(f"Could not remove old container {item}: {e}")
    
    with metadata_lock:
        distributed_metadata.clear()
    with ref_count_lock:
        block_reference_counts.clear()


    encryption_key = get_random_bytes(32)
    node_threads = []

    for node_id_str in node_addresses: # Uses the global node_addresses list
        with ref_count_lock: # Ensure node's entry is initialized
            if node_id_str not in block_reference_counts:
                block_reference_counts[node_id_str] = {}

        thread = threading.Thread(target=run_akita_node_thread, args=(sim_base_storage_path, encryption_key, node_id_str))
        thread.daemon = True # Allows main program to exit even if threads are running
        node_threads.append(thread)
        thread.start()

    logging.info(f"Launched {len(node_threads)} Akita node simulation threads. Waiting for announcements...")
    time.sleep(3) # Give nodes a moment to initialize and announce on Reticulum

    target_node = node_addresses[0] # Send all client requests to the first simulated node

    print("\n--- Akita MeshFS Client Demo ---")

    # Test Write
    file_content1 = "Hello Akita World! This is the first version of the notes."
    print(f"\nClient: WRITE file 'docs/notes.txt' with content: \"{file_content1[:30]}...\"")
    resp = akita_client_request(target_node, f"write:docs/notes.txt:{file_content1}")
    print(f"Client Response: {resp}")

    # Test Read
    print(f"\nClient: READ file 'docs/notes.txt'")
    resp = akita_client_request(target_node, "read:docs/notes.txt")
    print(f"Client Response: {resp}")
    if resp != f"read_response:{file_content1}": print("!! READ MISMATCH !!")


    # Test List (all items)
    print(f"\nClient: LIST all items (prefix '')")
    resp = akita_client_request(target_node, "list:") # Empty prefix means all
    print(f"Client Response: {resp}")

    # Test Create Snapshot
    print(f"\nClient: CREATE SNAPSHOT 'docs/notes.txt@snap1' from 'docs/notes.txt'")
    resp = akita_client_request(target_node, "snapshot_create:docs/notes.txt:docs/notes.txt@snap1")
    print(f"Client Response: {resp}")

    # Test List (filter for snapshots)
    print(f"\nClient: LIST snapshots only (type 'snapshot')")
    resp = akita_client_request(target_node, "list::snapshot") # Empty prefix, type snapshot
    print(f"Client Response: {resp}")
    try: # Basic check
        snap_list = json.loads(resp.split(":",1)[1])
        if not any(s['name'] == 'docs/notes.txt@snap1' for s in snap_list): print("!! SNAPSHOT LIST MISMATCH !!")
    except: pass


    # Modify original file
    file_content2 = "Original file has been MODIFIED after the snapshot was created."
    print(f"\nClient: DELETE 'docs/notes.txt' (to overwrite)") # Overwrite by delete then write
    resp_del_orig = akita_client_request(target_node, "delete:docs/notes.txt")
    print(f"Client Response (delete for overwrite): {resp_del_orig}")
    print(f"Client: WRITE 'docs/notes.txt' with new content: \"{file_content2[:30]}...\"")
    resp_write_new = akita_client_request(target_node, f"write:docs/notes.txt:{file_content2}")
    print(f"Client Response (write new): {resp_write_new}")


    # Read original file (should be new content)
    print(f"\nClient: READ modified file 'docs/notes.txt'")
    resp = akita_client_request(target_node, "read:docs/notes.txt")
    print(f"Client Response: {resp}")
    if resp != f"read_response:{file_content2}": print("!! MODIFIED READ MISMATCH !!")


    # Read snapshot (should be old content)
    print(f"\nClient: READ snapshot 'docs/notes.txt@snap1'")
    resp = akita_client_request(target_node, "read:docs/notes.txt@snap1")
    print(f"Client Response: {resp}")
    if resp != f"read_response:{file_content1}": print("!! SNAPSHOT READ MISMATCH (expected original content) !!")


    # Test Delete original file (snapshot should still exist)
    print(f"\nClient: DELETE file 'docs/notes.txt'")
    resp = akita_client_request(target_node, "delete:docs/notes.txt")
    print(f"Client Response: {resp}")

    # Read snapshot again (should still exist and be readable with original content)
    print(f"\nClient: READ snapshot 'docs/notes.txt@snap1' after original file was deleted")
    resp = akita_client_request(target_node, "read:docs/notes.txt@snap1")
    print(f"Client Response: {resp}")
    if resp != f"read_response:{file_content1}": print("!! SNAPSHOT READ MISMATCH AFTER ORIG DELETION !!")

    # Test Delete snapshot
    print(f"\nClient: DELETE snapshot 'docs/notes.txt@snap1'")
    resp = akita_client_request(target_node, "delete:docs/notes.txt@snap1")
    print(f"Client Response: {resp}")

    # Try to Read snapshot again (should be None now)
    print(f"\nClient: READ snapshot 'docs/notes.txt@snap1' after its deletion (should be None)")
    resp = akita_client_request(target_node, "read:docs/notes.txt@snap1")
    print(f"Client Response: {resp}")
    if resp != "read_response:None": print("!! DELETED SNAPSHOT READ MISMATCH !!")
    
    # List all items (should be empty if all were cleaned up)
    print(f"\nClient: LIST all items (should be empty now)")
    resp = akita_client_request(target_node, "list:")
    print(f"Client Response: {resp}")
    try:
        final_list = json.loads(resp.split(":",1)[1])
        if final_list: print(f"!! FINAL LIST NOT EMPTY: {final_list} !!")
    except: pass


    print("\n--- Akita MeshFS Demo Complete ---")
    logging.info("Demo complete. Main thread ending. Daemon threads will terminate automatically.")
    # For a very clean shutdown in a more complex app, you might:
    # 1. Signal node threads to stop (e.g., via an event).
    # 2. In node threads, tear down Reticulum links and exit their loops.
    # 3. Join all node threads in the main thread.
    # 4. Call RNS_INSTANCE.teardown()
    # For this script, daemon threads are sufficient.
