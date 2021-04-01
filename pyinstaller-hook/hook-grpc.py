from PyInstaller.utils.hooks import collect_data_files
datas = collect_data_files('grpc')

# Exception ignored in: 'grpc._cython.cygrpc.ssl_roots_override_callback'
# E0401 15:16:09.640000000  3236 src/core/lib/security/security_connector/ssl_utils.cc:553] assertion failed: pem_root_certs != nullptr