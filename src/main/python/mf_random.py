import numpy as np
import mf
import multiprocessing
import os
import os.path
import logging.config


log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'log_config.conf')
logging.config.fileConfig(log_file_path)
logger = logging.getLogger(__name__)


class SynergyRandomizer:

    def __init__(self, synergy):
        self.case_N = synergy.case_N
        self.control_N = synergy.control_N
        self.m1 = synergy.m1
        self.m2 = synergy.m2
        self.S = synergy.pairwise_synergy()
        self.empirical_distribution=np.zeros([1, 1])
        self.frequency_braket = np.array([0.001, 0.01, 0.05, 0.1, 0.3, 0.4, 0.6, 0.7, 0.8,
                            0.9])
        logger.info('randomizer initiated')

    def simulate(self, per_simulation=None, simulations=100, cpu=None,
                 job_id=0):
        TOTAL = self.case_N + self.control_N
        diag_prob = self.case_N / TOTAL
        phenotype_prob = np.sum(self.m1[:, 0:1], axis=1) / TOTAL
        if per_simulation is None:
            per_simulation = TOTAL
        self.empirical_distribution = create_empirical_distribution(diag_prob,
                 phenotype_prob, per_simulation, simulations, cpu, job_id)

    def simulate_approxi(self, phenotype_bucket=None,
                         per_simulation=None, simulations=100):
        TOTAL = self.case_N + self.control_N
        diag_prob = self.case_N / TOTAL
        if phenotype_bucket is not None:
            # create 10 buckets
            self.phphenotype_bucket = phenotype_bucket
        if per_simulation is None:
            per_simulation = TOTAL
        self.empirical_distribution = create_empirical_distribution(diag_prob,
              phenotype_bucket, per_simulation, simulations)

    def p_value(self, adjust=None):
        """
        Estimate p values for each observed phenotype pair by comparing the
        observed synergy score with empirical distributions created by random
        sampling.
        :param sampling: sampling times
        :return: p value matrix
        """
        # TODO: this is like Bonferroni, but not exactly
        if adjust == 'Bonferroni':
            test_times = len(np.triu(self.S))
            adjusted_S = self.S / test_times
        else:
            adjusted_S = self.S
        return p_value_estimate(adjusted_S, self.empirical_distribution,
                                'two.sided')

    def p_value_approxi(self, side='two.sided'):
        TOTAL = self.case_N + self.control_N
        M = self.S.shape[0]
        SIMULATION_TIMES = self.empirical_distribution.shape[2]
        ordered = np.sort(self.empirical_distribution, axis=-1)
        center = np.mean(self.empirical_distribution, axis=-1)
        # find the frequency of each phenotype
        p_freq = np.sum(self.m1, axis=1) / TOTAL
        assert len(p_freq) == M
        # find index for each phenotype
        for i in np.arange(M):
            # find closest position in the bucket for each phenotype
            position_in_bucket = closest_index(p_freq[i], self.phphenotype_bucket)
        p = np.zeros([M, M])
        for i in np.arange(M):
            for j in np.arange(M):
                # which distribution to look at
                target_dist = ordered[position_in_bucket[i],
                                      position_in_bucket[j]]
                observed = self.S[i, j]
                if side == 'two.sided':
                    p[i, j] = np.searchsorted(target_dist,
                              center - np.abs(center - observed), 'left') / \
                              SIMULATION_TIMES + \
                    1 - np.searchsorted(target_dist,
                    center + np.abs(center - observed)) / SIMULATION_TIMES
                elif side == 'left':
                    p[i, j] = np.searchsorted(target_dist, observed, 'right')\
                              / SIMULATION_TIMES
                else:
                    p[i, j] = 1 - np.searchsorted(target_dist, observed,
                                                  'left') / SIMULATION_TIMES

        return p


def p_value_estimate(observed, empirical_distribution, alternative='two.sided'):
    """
    Estimate P value of observed synergy scores from the empirical distribution.
    :param observed: a M x M matrix of observed synergy scores. M is the
     number of phenotypes being analyzed
    :param empirical_distribution: a M x M x n. Each vector of the M x M
    matrix represent an empirical distribution with size n.
    :param alternative: alternative hypothesis
    :return: a M x M matrix of which each element represent the p value for
    the observed synergy score of the phenotype pair.
    """

    M1, N1 = observed.shape
    M2, N2, P = empirical_distribution.shape
    assert M1 == M2
    assert N1 == N2
    ordered = np.sort(empirical_distribution, axis=-1)
    center = np.mean(empirical_distribution, axis=-1)
    if alternative == 'two.sided':
        return matrix_searchsorted(ordered, center - np.abs(observed - center),
                                   side='right') / P + \
               1 - \
               matrix_searchsorted(ordered, center + np.abs(observed - center),
                                   side='left') / P
    elif alternative == 'left':
        return matrix_searchsorted(ordered, observed, side='right') / P
    elif alternative == 'right':
        return 1 - matrix_searchsorted(ordered, observed, side='left') / P
    else:
        raise ValueError


def matrix_searchsorted(ordered, query, side='left'):
    """
    A matrix implementation of Numpy searchsorted. It searches the 3D array
    for each element of the 2D query, and returns the indices as a
    2D array.
    :param ordered: an ordered 3D array with shape M x M x n
    :param query: a 2D array with shape M x M
    :return: a 2D array, each element represent the index where the query
    element should be inserted into the vector at the corresponding place in
    the ordered 3D array.
    """
    (m, n) = query.shape
    assert m == ordered.shape[0]
    assert n == ordered.shape[1]
    idx = np.zeros([m, n])
    for i in np.arange(m):
        for j in np.arange(n):
            idx[i,j] = np.searchsorted(ordered[i, j, :], query[i, j], side)
    return idx


def create_empirical_distribution(diag_prevalence, phenotype_prob,
                                  sample_per_simulation, SIMULATION_SIZE,
                                  cpu=None, job_id=0):
    """
    Create empirical distributions for each phenotype pair.
    :param diag_case_prob: a scalar for the prevalence of the diagnosis under
    study
    :param phenotype_prob: a size M vector for the prevalence of the phenotypes
    under study
    :param sample_per_simulation: number of samples for each simulation
    :param SIMULATION_SIZE: total simulations
    :return: a M x M x SIMULATION_SIZE matrix for the empirical distributions
    """
    logger.info('number of CPU: {}'.format(os.cpu_count()))
    if cpu is None:
        cpu = os.cpu_count()
    workers = multiprocessing.Pool(cpu)
    logger.info(f'number of workers created: {cpu}')
    results = [workers.apply_async(synergy_random, args=(diag_prevalence,
                                                        phenotype_prob,
                                                        sample_per_simulation,
                                                        i + job_id*SIMULATION_SIZE))
         for i in np.arange(SIMULATION_SIZE)]
    workers.close()
    workers.join()
    assert(len(results) == SIMULATION_SIZE)
    empirical_distribution = np.stack([res.get() for res in results], axis=-1)

    return empirical_distribution


def synergy_random(disease_prevalence, phenotype_prob, sample_size,
                   seed=None):
    """
    Simulate disease condition and phenotype matrix with provided
    probability distributions and calculate the resulting synergy.
    :param disease_prevalence: a scalar representation of the disease prevalence
    :param phenotype_prob: a size M vector representing the observed
    prevalence of phenotypes
    :param sample_size: number of cases to simulate
    :return: a M x M matrix representing the pairwise synergy from the
    simulated disease conditions and phenotype profiles.
    """
    if seed is not None:
        np.random.seed(seed)
    mocked = mf.Synergy(disease='mocked', phenotype_list=np.arange(len(
        phenotype_prob)))
    BATCH_SIZE = 100
    M = len(phenotype_prob)
    total_batches = int(np.ceil(sample_size / BATCH_SIZE))
    logger.debug('start simulation: {} '.format(seed))
    for i in np.arange(total_batches):
        logger.debug(f'add batch {i} -> simulation {seed}')
        if i == total_batches - 1:
            actual_batch_size = sample_size - BATCH_SIZE * (i - 1)
        else:
            actual_batch_size = BATCH_SIZE
        d = (np.random.uniform(0, 1, actual_batch_size) <
             disease_prevalence).astype(int)
        # the following is faster than doing choice with loops
        P = np.random.uniform(0, 1, actual_batch_size*M).reshape([
            actual_batch_size, M])
        P = (P < phenotype_prob.reshape([1, M])).astype(int)
        mocked.add_batch(P, d)
    logger.debug('end simulation {}'.format(seed))
    return mocked.pairwise_synergy()


def closest_index(query, ordered_positivelist, transform='log10'):
    assert (ordered_positivelist > 0).all()
    if transform == 'log10':
        adjust = 0.00001
        query = np.log10(query + adjust)
        ordered_positivelist = np.log10(ordered_positivelist)

    if query <= ordered_positivelist[0]:
        return 0
    elif query >= ordered_positivelist[-1]:
        return len(ordered_positivelist)
    else:
        for i in np.arange(0, len(ordered_positivelist) - 1):
            if query > ordered_positivelist[i + 1]:
                continue
            print('query {}'.format(query))
            to_left = query - ordered_positivelist[i]
            print('distance to left: {}'.format(to_left))
            to_right = ordered_positivelist[i + 1] - query
            print('distance to right: {}'.format(to_right))
            if to_left <= to_right:
                return i
            else:
                return i + 1


if __name__=='__main__':
    phenotype_p = np.array([0.001, 0.01, 0.05, 0.1, 0.3, 0.4, 0.6, 0.7, 0.8,
                            0.9])
    N = 5000
    dist = create_empirical_distribution(0.3, phenotype_p,
                                  1000, N)
    print(np.sum(dist, axis=-1) / N)