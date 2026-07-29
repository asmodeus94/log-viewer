import multiprocessing
from log_viewer.main import main

if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
