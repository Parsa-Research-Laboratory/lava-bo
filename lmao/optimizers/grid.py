from lava.magma.core.model.py.model import PyAsyncProcessModel
from lava.magma.core.decorator import implements, requires, tag
from lava.magma.core.resources import CPU
from lava.magma.core.model.py.type import LavaPyType
from lava.magma.core.model.py.ports import PyInPort, PyOutPort
from lava.magma.core.process.variable import Var
from lava.magma.core.sync.protocols.async_protocol import AsyncProtocol

import itertools
import numpy as np
from omegaconf import DictConfig
import os
from skopt import Optimizer
from skopt.space import Space, Integer, Categorical
import time

from lmao.optimizers.base import BaseOptimizerProcess


class GridOptimizerProcess(BaseOptimizerProcess):
    """
    A class representing the process using grid search for optimizing
    black-box functions.

    This class extends the BaseOptimizerProcess class and provides additional
    functionality specific to the grid search implementation.

    Attributes:
        TODO Finish Documentation

    Args:
        config (DictConfig): The configuration for the optimization process.
        search_space (Space): The search space for the optimization process.
        **kwargs: Additional keyword arguments to be based to the
            BaseOptimizerProcess constructor.
    """
    def __init__(self, config: DictConfig, search_space: Space, **kwargs):
        """
        Initialize a GridOptimizer Process

        Args:
            config (DictConfig): The configuration for the optimization process.
            search_space (Space): The search space for the optimization process.
            **kwargs: Additional keyword arguments to be passed to the
                BaseOptimizer constructor
        """

        assert isinstance(config, DictConfig), \
            f"config must be a DictConfig object; got {type(config)}"
        assert isinstance(search_space, Space), \
            f"search_space must be a Space object; got {type(search_space)}"

        super().__init__(num_params=search_space.n_dims,
                         num_processes=config.get("num_processes", 1),
                         num_repeats=config.get("num_repeats", 1),
                         num_outputs=config.get("num_outputs", 1),
                         **kwargs)

        # -------------------------
        # Configuration Parameters
        # -------------------------
        self.max_iterations = Var(
            shape=(1,),
            init=config.max_iterations
        )

        self.seed = Var(
            shape=(1,),
            init=config.seed
        )

        # ------------------------
        # Internal State Variables
        # ------------------------
        self.finished = Var(shape=(1,), init=0)
        self.process_ticker = Var(shape=(1,), init=0)
        self.time_step = Var(shape=(1,), init=-1)

        # ------------------------
        # Logging Variables
        # ------------------------
        x_log_shape: tuple = (
            self.max_iterations.get(),
            self.num_params.get()
        )
        self.x_log = Var(
            shape=x_log_shape,
            init=np.zeros(x_log_shape)
        )
        y_log_shape: tuple = (
            self.max_iterations.get(),
            self.num_outputs.get()
        )
        self.y_log = Var(
            shape=y_log_shape,
            init=np.zeros(y_log_shape)
        )
        self.y_log_min = Var(
            shape=y_log_shape,
            init=np.zeros(y_log_shape)
        )
        time_log_shape: tuple = (self.max_iterations.get(),)
        self.time_log = Var(
            shape=time_log_shape,
            init=np.zeros(time_log_shape)
        )

        # ------------------
        # Search Space
        # ------------------
        search_space_shape: tuple = (search_space.n_dims,4)
        local_search_space: np.ndarray = np.zeros(search_space_shape, dtype=np.float32)
        global global_search_space_values
        global_search_space_values = []

        for i, dim in enumerate(search_space.dimensions):
            if isinstance(dim, Integer):
                local_search_space[i, 0] = dim.low
                local_search_space[i, 1] = dim.high
                local_search_space[i, 2] = 1.0
                global_search_space_values.append([])
            elif isinstance(dim, Categorical):
                local_search_space[i, 2] = 2.0
                global_search_space_values.append(dim.categories)
            else:
                raise ValueError(f"Unsupported dimension type: {type(dim)}")
            
        self.search_space = Var(
            shape=search_space_shape,
            init=local_search_space
        )

@implements(proc=GridOptimizerProcess, protocol=AsyncProtocol)
@requires(CPU)
@tag("floating_pt")
class PyAsyncGridOptimizerModel(PyAsyncProcessModel):
    """
    A class representing a PyAsyncGridOptimizerModel.

    The class is responsible for optimizing a function using Gaussian Process
    Regression (GPR). It inherits from the PyAsyncProcessModel class.

    Attributes:
        input_port (PyInPort): The input port for receiving data.
        output_port (PyOutPort): The output port for sending data.
        num_params (int): The number of parameters in the optimization problem.
        num_outputs (int): The number of outputs in the optimization problem.
        num_repeats (int): The number of times to repeat the optimization
            process.
        max_iterations (int): The maximum number of iterations for the
            optimization process.
        seed (int): The seed for the random number generation.
        finished (int): Flag indicating whether the optimization process
            has finished.
        time_step (int): The current time step of the optimization process.
        x_log (np.ndarray): Log of the evaluated parameters
        y_log (np.ndarray): The 'quality' of each parameter combination
    """

    # ----------------------------------
    # Initialize input and output ports
    # for each unique process
    # ----------------------------------
    num_processes: str = os.environ["LAVA_BO_NUM_PROCESSES"]
    num_processes: int = int(num_processes)
    for i in range(num_processes):
        exec(f"input_port_{i} = LavaPyType(PyInPort.VEC_DENSE, np.float32)")
        exec(f"output_port_{i} = LavaPyType(PyOutPort.VEC_DENSE, np.float32)")

    # ------------------------
    # Parent Class Parameters
    # ------------------------
    num_params = LavaPyType(int, int)
    num_processes = LavaPyType(int, int)
    num_outputs = LavaPyType(int, int)
    num_repeats = LavaPyType(int, int)

    # ------------------------
    # Configuration Parameters
    # ------------------------
    max_iterations = LavaPyType(int, int)
    seed = LavaPyType(int, int)
    search_space = LavaPyType(np.ndarray, object)

    # ------------------------
    # Internal State Variables
    # ------------------------
    finished = LavaPyType(int, int)
    process_ticker = LavaPyType(int, int)
    time_step = LavaPyType(int, int)

    # ------------------------
    # Logging Variables
    # ------------------------
    x_log = LavaPyType(np.ndarray, np.float32)
    y_log = LavaPyType(np.ndarray, np.float32)
    y_log_min = LavaPyType(np.ndarray, np.float32)
    time_log = LavaPyType(np.ndarray, np.float32)

    def run_async(self):
        """
        Run the optimization process asynchronously.

        This method continuously runs the optimization process until a pause or
        stop command is received. It sends an initial point to prime the system
        and then iteratively received new data, updates the optimizer, and
        sends new points to evaluate.
        """

        while True:
            if self.check_for_pause_cmd() or self.check_for_stop_cmd():
                return
            
            if self.time_step == -1:
                decoded_search_space = []
                for i in range(self.search_space.shape[0]):
                    dim = self.search_space[i]
                    if dim[2] == 1.0:
                        low_bound = int(dim[0])
                        high_bound = int(dim[1])
                        range_values = list(range(low_bound, high_bound + 1))
                        decoded_search_space.append(range_values)
                    elif dim[2] == 2.0:
                        low_bound = int(0.0)
                        high_bound = len(global_search_space_values[i])
                        range_values = list(range(low_bound, high_bound))
                        decoded_search_space.append(range_values)
                    else:
                        raise ValueError(f"Unsupported dimension type: {dim[0]}")
                    
                self.grid_points = list(itertools.product(*decoded_search_space))
                    
                # send an initial point to each of the process
                self.unknown_point_cache: list = None # TODO
                # output_data_list = [output_data_list]
                for i in range(self.num_processes):
                    if len(self.grid_points) > 0:
                        output_port: PyOutPort = eval(f"self.output_port_{i}")
                        next_point: tuple = self.grid_points[-1]
                        next_point = list(next_point)
                        next_point = np.array(next_point)

                        for idx in range(len(next_point)):
                            if self.search_space[idx][2] == 2.0:
                                next_point[idx] = global_search_space_values[idx][next_point[idx]]

                        output_data: np.ndarray = np.array(next_point)                
                        output_port.send(output_data)
                        del self.grid_points[-1]

                # Iterate to 0 to get out of the initialization and process
                # priming state
                self.time_step += 1

            if self.time_step < self.max_iterations:
                
                input_port: PyInPort = eval(f"self.input_port_{self.process_ticker}")
                output_port: PyOutPort = eval(f"self.output_port_{self.process_ticker}")
                self.process_ticker = (self.process_ticker + 1) % self.num_processes
                if input_port.probe():
                    start_time: float = time.time()
                    new_data: np.ndarray = input_port.recv()

                    print("time step: ", self.time_step)
                    print("max iterations: ", self.max_iterations)
                    print("new data: ", new_data)

                    x = new_data[:self.num_params]
                    y = new_data[self.num_params:]

                    self.x_log[self.time_step, :] = x
                    self.y_log[self.time_step, :] = y
                    self.y_log_min[self.time_step, :] = np.min(self.y_log[:self.time_step+1], axis=0)
                    self.time_step += 1


                    if len(self.grid_points) > 0:
                        next_point: tuple = self.grid_points[-1]
                        output_data: np.ndarray = np.array(next_point)

                        for idx in range(len(output_data)):
                            if self.search_space[idx][2] == 2.0:
                                output_data[idx] = global_search_space_values[idx][int(output_data[idx])]

                        output_port.send(output_data)
                        del self.grid_points[-1]

                    self.time_log[self.time_step - 1] = time.time() - start_time                    
            else:
                self.finished = 1
                    
                
                    
                    
                    
                
                    


                    
