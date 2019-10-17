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

    def __init__(self, synergy_to_simulate):
        synergy = synergy_to_simulate
        self.case_N = synergy.case_N
        self.control_N = synergy.control_N
        self.m1 = synergy.m1
        self.m2 = synergy.m2
        self.S = synergy.pairwise_synergy()
        logger.info('randomizer initiated')

    def simulate(self, per_simulation=None, simulations=100, cpu=None,
                 job_id=0):
        TOTAL = self.case_N + self.control_N
        diag_prob = self.case_N / TOTAL
        phenotype_prob1 = np.sum(self.m1['set1'][:, 0:1], axis=1) / TOTAL
        phenotype_prob2 = np.sum(self.m1['set2'][:, 0:1], axis=1) / TOTAL
        if per_simulation is None:
            per_simulation = TOTAL
        self.empirical_distribution = create_empirical_distribution(diag_prob,
              phenotype_prob1, phenotype_prob2, per_simulation, simulations,
              cpu, job_id)

    def p_value(self, adjust=None):
        """
        Estimate p values for each observed phenotype pair by comparing the
        observed synergy score with empirical distributions created by random
        sampling.
        :param sampling: sampling times
        :return: p value matrix
        """
        p = p_value_estimate(self.S, self.empirical_distribution, 'two.sided')
        if adjust == 'Bonferroni':
            test_times = len(np.triu(self.S))
            p = p * test_times

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


def synergy_random(disease_prevalence, phenotype_prob1, phenotype_prob2,
    sample_size, seed=None):
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
    mocked = mf.SummaryXYz(X_names=np.arange(len(phenotype_prob1)),
                        Y_names=np.arange(len(phenotype_prob2)),
                        z_name='mocked')
    BATCH_SIZE = 100
    M1 = len(phenotype_prob1)
    M2 = len(phenotype_prob2)
    total_batches = int(np.ceil(sample_size / BATCH_SIZE))
    logger.debug('start simulation: {} '.format(seed))
    for i in np.arange(total_batches):
        if i % 100 == 0:
            logger.debug('add batch {} -> simulation {}'.format(i, seed))
        if i == total_batches - 1:
            actual_batch_size = sample_size - BATCH_SIZE * (i - 1)
        else:
            actual_batch_size = BATCH_SIZE
        d = (np.random.uniform(0, 1, actual_batch_size) <
             disease_prevalence).astype(int)
        # the following is faster than doing choice with loops
        P1 = np.random.uniform(0, 1, actual_batch_size*M1).reshape([
            actual_batch_size, M1])
        P1 = (P1 < phenotype_prob1.reshape([1, M1])).astype(int)
        P2 = np.random.uniform(0, 1, actual_batch_size * M2).reshape([
            actual_batch_size, M2])
        P2 = (P2 < phenotype_prob2.reshape([1, M2])).astype(int)
        mocked.add_batch(P1, P2, d)
    logger.debug('end simulation {}'.format(seed))

    return mf.MutualInfoXYz(mocked).pairwise_synergy()


def create_empirical_distribution(diag_prevalence, phenotype_prob1,
                                   phenotype_prob2, sample_per_simulation,
                                   SIMULATION_SIZE, cpu=None, job_id=0):
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
    logger.info('number of workers created: {}'.format(cpu))
    results = [workers.apply_async(synergy_random, args=(diag_prevalence,
                                                        phenotype_prob1,
                                                        phenotype_prob2,
                                                        sample_per_simulation,
                                                        i + job_id*SIMULATION_SIZE))
         for i in np.arange(SIMULATION_SIZE)]
    workers.close()
    workers.join()
    assert(len(results) == SIMULATION_SIZE)
    empirical_distribution = np.stack([res.get() for res in results], axis=-1)

    return empirical_distribution


if __name__=='__main__':
    phenotype_p = np.array([0.001, 0.01, 0.05, 0.1, 0.3, 0.4, 0.6, 0.7, 0.8,
                            0.9])
    N = 5000
    dist = create_empirical_distribution(0.3, phenotype_p, phenotype_p,
                                  1000, N)
    print(np.sum(dist, axis=-1) / N)