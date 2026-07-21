import multiprocessing
from log_reader.main import main

if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
