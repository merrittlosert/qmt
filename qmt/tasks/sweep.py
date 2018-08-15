import dask
import dask.distributed
from six import iteritems


class SweepManager(object):
    """
    Represents a sweep over simulation input values.

    More precisely, represents a subset of the cartesian product of the input domains.

    Attributes:
        sweep_list: The sweep elements represented by this

    """

    # Abstraction function:
    # Each entry in the list self.sweep_list is an element in the product of input domains.
    # Each entry has the form {tag1: value1, ... tagN: valueN}
    # mapping SweepTag objects to values in each of the input domains.

    def __init__(self, sweep_list, dask_client=None):
        """
        Constructs a new SweepManager directly from sweep entries.
        :param sweep_list: a list representing values in the cartesian
        product of input domains. Each entry has the form {tag1: value1, ... tagN: valueN}
        :param dask_client: the dask client to run the sweep on. Defaults to
        a client on the local machine.
        """
        self.sweep_list = sweep_list
        assert isinstance(self.sweep_list, list) and \
               isinstance(self.sweep_list[0], dict), \
            'sweep_list must be a list of dicts.'
        assert all([set(sweep_list[0].keys()) == \
                    set(x.keys()) for x in sweep_list]), \
            'All dicts in sweep_list must have same keys.'

        if dask_client is None:
            dask_client = dask.distributed.Client(processes=False)  # client on the local machine

        self.dask_client = dask_client

        self.results = None

    # TODO test
    def createEmptySweep(self, dask_client=None):
        return SweepManager([{}], dask_client)

    @staticmethod
    def construct_cartesian_product(params_to_product):
        """
        Constructs a new SweepManager from data values for each input.
        :param params_to_product: a dictionary mapping tags to lists of input values
        :return: a SweepManager over the cartesian product of the
        input values in each input list
        """
        assert type(params_to_product) == type({}), \
            'params_to_product must be a dict of lists.'

        def merge_two_dicts(x, y):
            """
            Creates a new dictionary which contains both the mapping
            in x and the mapping in y, overwriting keys in y if necessary.
            :param x: the first dict to merge
            :param y: the second dict to merge
            :return: a union of x and y
            """
            z = x.copy()  # start with x's keys and values
            z.update(y)  # modifies z with y's keys and values & returns None
            return z

        result = [{}]
        for key in params_to_product:
            pool = params_to_product[key]
            result = [merge_two_dicts(x, {key: y}) for x in result for y in pool]
        return SweepManager(result)

    def run(self, task):
        """
        Runs the sweep represented by this.

        :param task: the top level task to run
        :return: the ReducedSweepFutures representing the result
        """

        def set_sweep_manager(current_task):
            current_task.sweep_manager = self
            for child_task in current_task.previous_tasks:
                set_sweep_manager(child_task)

        set_sweep_manager(task)
        return task._run()

    def __str__(self):
        return "<SweepManager with " + str(len(self.sweep_list)) + " entries>"


class ReducedSweepWithData(object):
    def __init__(self, sweep, data):
        self.sweep = sweep
        self._data = data
        self.tagged_value_list = sweep.tagged_value_list


    @staticmethod
    def sweep_and_empty_data_from_manager_and_tags(sweep_manager, tags):
        sweep = ReducedSweep.create_from_manager_and_tags(sweep_manager, tags)
        data = sweep.empty_data()
        return sweep, data

    def _get_datum(self, total_index):
        return self._data[self.sweep.convert_to_reduced_index(total_index)]

    def __iter__(self):
        return iter(self._data)

    def __str__(self):
        return str(self._data)


class ReducedSweepFutures(ReducedSweepWithData):
    """
    Contains sweep information and results in the form of Dask futures.
    """

    def __init__(self, sweep, futures):
        super(ReducedSweepWithData, self).__init__(sweep, futures)
        self.futures = self._data

    # TODO deprecate?
    # def wait(self):
    #     return dask.distributed.wait(self.futures)

    # TODO deprecate?
    # @staticmethod
    # def get_each_element_function(self):
    #     return self.sweep, [future.result() for future in self.futures]

    # TODO deprecate? No usages.
    # def get_gathered_results(self):
    #     gathered =[]
    #     for future in self.futures:
    #         gathered.append(future.result())
    #
    #     return gathered

    def calculate_completed_results(self):
        completed_results = []
        for future in self.futures:
            completed_results.append(future.result())

        return ReducedSweepResults(self.sweep, completed_results)
        # if not self.results:
        #     for future in self.futures:
        #         self.results.append(future.result())

    def get_completed_result(self, total_index):
        return self._get_datum(total_index).result()


class ReducedSweepDelayed(ReducedSweepWithData):
    def __init__(self, sweep, dask_client):
        self.delayed_results = sweep.empty_data()
        super(ReducedSweepWithData, self).__init__(sweep, self.delayed_results)
        self.dask_client = dask_client

    @staticmethod
    def create_from_reduced_sweep_and_manager(sweep, manager):
        sweep = sweep
        dask_client = manager.dask_client
        # contains a ReducedSweep and forwards its methods
        # needs to take the dask client as well
        # has methods for producing the list of delayed objects
        # and reducing over itself using an arbitrary function.
        # This reduction can then be used in conjunction with the ReducedSweep
        # contained in this.
        return ReducedSweepDelayed(sweep, dask_client)

    # TODO deprecate? No uses.
    # def copy_empty(self):
    #     return self.__init__(self.sweep, self.dask_client)

    def get_object(self, total_index):
        return self._get_datum(total_index)

    def calculate_futures(self, resources):
        """
        Triggers the execution of the sweep.
        """

        assert self.delayed_results[0] is not None
        futures = []
        for delayed_result in self.delayed_results:
            futures.append(self.dask_client.compute(delayed_result, resources=resources))

        return ReducedSweepFutures(self.sweep, futures)

    def visualize_entire_sweep(self, filename=None):
        delayed_proxy = dask.delayed(id)(self.delayed_results)
        if filename:
            delayed_proxy.visualize(filename=filename)
        return delayed_proxy.visualize()

    def visualize_single_sweep_element(self, filename=None):
        if filename:
            self.delayed_results[0].visualize(filename=filename)
        return self.delayed_results[0].visualize()


class ReducedSweepResults(ReducedSweepWithData):
    def __init__(self, sweep, results):
        super(ReducedSweepWithData, self).__init__(sweep, results)
        self.results = self._data

    def create_empty_from_manager_and_tags(self, manager, tags):
        sweep = ReducedSweep.create_from_manager_and_tags(manager, tags)
        empty_results = sweep.empty_data()
        return ReducedSweepResults(sweep, empty_results)


class ReducedSweep(object):
    """
    Represents the output of a sweep, restricted to the relevant subset of the input tags.

    SweepHolder represents the restriction of a sweep over many parameters,
    to a sweep over a subset of these parameters that is the input for a particular task.

    Public Attributes:
        sweep_manager: The whole sweep being performed.
        list_of_tags: The tags corresponding to the part of the sweep that this
            SweepHolder is restricted to.
    """

    def __init__(self, list_of_tags, sweep_list, tagged_value_list, index_in_sweep):
        self.list_of_tags = list_of_tags
        self.sweep_list = sweep_list
        self.tagged_value_list = tagged_value_list
        self._index_in_sweep = index_in_sweep

        # contains info about the sweep points in the reduced sweep
        # and the mapping of the reduced sweep to the total sweep

    @staticmethod
    def create_from_manager_and_tags(sweep_manager, list_of_tags):
        """
                Constructs the restriction of the sweep sweep_manager to the input tagged by list_of_tags.

                :param sweep_manager: The sweep to restrict
                :param list_of_tags: The tags to restrict the input to
                """

        sweep_list = sweep_manager.sweep_list

        tagged_value_list = []
        index_in_sweep = []
        # The following nested for-loops could probably be made more
        # elegant, but it works for now

        # For each point in the total sweep
        for i, sweep_point in enumerate(sweep_list):

            # Check whether it is a new point with respect to the restricted
            # list of tags in self.list_of_tags, or whether this combination
            # of the relevant tags has already been encountered in the sweep
            new_point = True
            point_small_index = None
            for j, small_sweep_point in enumerate(tagged_value_list):
                # TODO - this should be done in a way that is also py27 compatible. Using
                # six.iteritems doesn't work.
                if small_sweep_point.items() <= sweep_point.items():
                    new_point = False
                    point_small_index = j

            # If the sweep point is a new point,
            # add the corresponding sweep element {tag1: value1, ... tagN: valueN}
            # to the list of values
            if new_point:
                tagged_value_list += [dict((tag, sweep_point[tag]) for tag in list_of_tags)]
                index_in_sweep += [[i]]
            else:
                index_in_sweep[point_small_index] += [i]

        index_in_sweep = index_in_sweep
        delayed_object_list = [None] * len(index_in_sweep)

        return ReducedSweep(list_of_tags, sweep_list, tagged_value_list, index_in_sweep)

    def convert_to_reduced_index(self, total_index):
        """
        Converts the index of a sweep element in the total sweep to the corresponding index in the reduced sweep.
        :param total_index: index of a sweep element in the total sweep
        :return: corresponding index in the reduced sweep
        """
        reduced_index = [total_index in sublist for sublist in self._index_in_sweep].index(True)
        return reduced_index

    def convert_to_total_indices(self, reduced_index):
        """
        Gets a list of indices in the total sweep corresponding to the index of an element in the reduced sweep.

        :param reduced_index: index of an element in the reduced sweep
        :return: list of corresponding indices in the total sweep
        """
        total_index = self._index_in_sweep[reduced_index]
        return total_index

    def __len__(self):
        return len(self._index_in_sweep)

    def empty_data(self):
        return [None for i in range(len(self))]


# TODO refactor the creation of sweeps and sweepTags to make script less noisy
class SweepTag(object):
    """
    Unique tag for id'ing parameters in the sweep.
    """

    def __init__(self, tag_name):
        self.tag_name = tag_name
        self.tag_function = lambda x: x

    def __str__(self):
        return self.tag_name

    def __repr__(self):
        return self.tag_name

    def __eq__(self, other):
        if isinstance(other, SweepTag):
            return self.tag_name == other.tag_name
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.tag_name)

    def __add__(self, other):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: self.tag_function(x) + other
        return out

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: self.tag_function(x) - other
        return out

    def __rsub__(self, other):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: other - self.tag_function(x)
        return out

    def __mul__(self, other):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: self.tag_function(x) * other
        return out

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: self.tag_function(x) / other
        return out

    def __pow__(self, other):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: self.tag_function(x) ** other
        return out

    def __neg__(self):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: -self.tag_function(x)
        return out

    def __abs__(self):
        out = SweepTag(self.tag_name)
        out.tag_function = lambda x: abs(self.tag_function(x))
        return out

    def replace(self, value):
        return self.tag_function(value)


def gen_tag_extract(nested_dictionary_of_tags):
    """
    Extract all tags from nested dictionary that may have tags
    as values at any level
    :param nested_dictionary_of_tags: exactly what it sounds like
    :return: a generator that yields all the tags in the dictionary
    """
    # if hasattr(nested_dictionary_of_tags, 'iteritems'): # doesn't work with python3
    for k, v in iteritems(nested_dictionary_of_tags):
        if isinstance(v, SweepTag):
            yield v
        if isinstance(v, dict):
            for result in gen_tag_extract(v):
                yield result
        if isinstance(v, list):
            for result in gen_tag_extract_list(v):
                yield result


def gen_tag_extract_list(nested_list_of_tags):
    """
    Extract all tags from nested dictionary that may have tags
    as values at any level
    :param nested_list_of_tags: exactly what it sounds like
    :return: a generator that yields all the tags in the dictionary
    """
    # if hasattr(nested_dictionary_of_tags, 'iteritems'): # doesn't work with python3
    for v in nested_list_of_tags:
        if isinstance(v, SweepTag):
            yield v
        if isinstance(v, dict):
            for result in gen_tag_extract(v):
                yield result
        if isinstance(v, list):
            for result in gen_tag_extract_list(v):
                yield result


def replace_tag_with_value(name_to_tag_mapping, tag, new_value):
    """
    Returns a copy of name_to_tag_mapping with values of tag replaced with new_value.
    :param name_to_tag_mapping: Dictionary mapping parameter names to SweepTags.
    :param tag: The SweepTag to replace.
    :param new_value: the value to replace it with.
    :return: a copy of name_to_tag_mapping with values of tag replaced with new_value
    """
    var_copy = {}
    # if hasattr(name_to_tag_mapping, 'iteritems'):
    for k, v in iteritems(name_to_tag_mapping):
        if v == tag:
            var_copy[k] = v.replace(new_value)
        elif isinstance(v, dict):
            var_copy[k] = replace_tag_with_value(v, tag, new_value)
        elif isinstance(v, list):
            var_copy[k] = replace_tag_with_value_list(v, tag, new_value)
        else:
            var_copy[k] = v
    return var_copy


def replace_tag_with_value_list(name_to_tag_mapping, tag, new_value):
    """
    Returns a copy of name_to_tag_mapping with values of tag replaced with new_value.
    :param name_to_tag_mapping: Dictionary mapping parameter names to SweepTags.
    :param tag: The SweepTag to replace.
    :param new_value: the value to replace it with.
    :return: a copy of name_to_tag_mapping with values of tag replaced with new_value
    """
    var_copy = []
    # if hasattr(name_to_tag_mapping, 'iteritems'):
    for v in name_to_tag_mapping:
        if v == tag:
            var_copy += [v.replace(new_value)]
        elif isinstance(v, dict):
            var_copy += [replace_tag_with_value(v, tag, new_value)]
        elif isinstance(v, list):
            var_copy += [replace_tag_with_value_list(v, tag, new_value)]
        else:
            var_copy += [v]
    return var_copy
