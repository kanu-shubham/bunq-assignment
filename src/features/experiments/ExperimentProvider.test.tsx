import React from 'react';
import { render, screen, act } from '@testing-library/react';
import {
  ExperimentProvider,
  ExperimentProviderProps,
  ExperimentSwitch,
  useExperiment,
} from './ExperimentProvider';
import { Experiment } from './types';

const ratingCopy: Experiment<{ title: string }> = {
  key: 'rating-copy',
  status: 'RUNNING',
  traffic: 1,
  variants: [
    { key: 'control', weight: 1, payload: { title: 'How would you rate this feature?' } },
    { key: 'treatment', weight: 1, payload: { title: 'Enjoying this feature?' } },
  ],
};

function Probe({ exposed }: { exposed?: boolean }): JSX.Element {
  const { variant, reason, enrolled } = useExperiment<{ title: string }>('rating-copy', { exposed });
  return <span data-testid="probe">{`${variant}/${reason}/${enrolled}`}</span>;
}

function setup(
  props: Partial<ExperimentProviderProps> = {},
  children: React.ReactNode = <Probe />,
) {
  const track = jest.fn();
  const utils = render(
    <ExperimentProvider
      unitId="user-1"
      experiments={[ratingCopy as Experiment<unknown>]}
      track={track}
      now={() => 1700000000000}
      {...props}
    >
      {children}
    </ExperimentProvider>,
  );
  return { ...utils, track };
}

describe('<ExperimentProvider /> + useExperiment', () => {
  test('assigns a variant and logs exactly one exposure', () => {
    const { track } = setup();
    expect(screen.getByTestId('probe').textContent).toMatch(/^(control|treatment)\/ENROLLED\/true$/);
    expect(track).toHaveBeenCalledTimes(1);
    expect(track).toHaveBeenCalledWith({
      experimentKey: 'rating-copy',
      variant: expect.any(String),
      unitId: 'user-1',
      timestamp: 1700000000000,
    });
  });

  test('re-renders do not re-log exposure', () => {
    const { track, rerender } = setup();
    for (let i = 0; i < 5; i += 1) {
      rerender(
        <ExperimentProvider unitId="user-1" experiments={[ratingCopy as Experiment<unknown>]} track={track}>
          <Probe />
        </ExperimentProvider>,
      );
    }
    expect(track).toHaveBeenCalledTimes(1);
  });

  test('two components reading the same experiment log one exposure between them', () => {
    const { track } = setup({}, (
      <>
        <Probe />
        <Probe />
      </>
    ));
    expect(track).toHaveBeenCalledTimes(1);
  });

  test('exposed={false} renders the variant but withholds the exposure', () => {
    const { track } = setup({}, <Probe exposed={false} />);
    expect(screen.getByTestId('probe').textContent).toContain('ENROLLED');
    expect(track).not.toHaveBeenCalled();
  });

  test('held-back units are not exposed', () => {
    const { track } = setup({
      experiments: [{ ...ratingCopy, traffic: 0 } as Experiment<unknown>],
    });
    expect(screen.getByTestId('probe').textContent).toBe('control/OUT_OF_TRAFFIC/false');
    expect(track).not.toHaveBeenCalled();
  });

  test('forced (QA) assignments never enter the analysis', () => {
    const { track } = setup({ overrides: { 'rating-copy': 'treatment' } });
    expect(screen.getByTestId('probe').textContent).toBe('treatment/FORCED/false');
    expect(track).not.toHaveBeenCalled();
  });

  test('unknown experiment key degrades to control instead of crashing', () => {
    const { track } = setup({ experiments: [] });
    expect(screen.getByTestId('probe').textContent).toBe('control/UNKNOWN_EXPERIMENT/false');
    expect(track).not.toHaveBeenCalled();
  });

  test('a new unit id (login) produces a fresh exposure', () => {
    const track = jest.fn();
    const tree = (unitId: string): JSX.Element => (
      <ExperimentProvider unitId={unitId} experiments={[ratingCopy as Experiment<unknown>]} track={track}>
        <Probe />
      </ExperimentProvider>
    );
    const { rerender } = render(tree('anon-1'));
    act(() => {
      rerender(tree('user-99'));
    });
    expect(track).toHaveBeenCalledTimes(2);
    expect(track.mock.calls[0][0].unitId).toBe('anon-1');
    expect(track.mock.calls[1][0].unitId).toBe('user-99');
  });

  test('missing provider fails loudly', () => {
    const error = jest.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => render(<Probe />)).toThrow(/ExperimentProvider/);
    error.mockRestore();
  });
});

describe('<ExperimentSwitch />', () => {
  test('renders the branch for the assigned variant', () => {
    setup({ overrides: { 'rating-copy': 'treatment' } }, (
      <ExperimentSwitch
        name="rating-copy"
        variants={{ control: <p>old copy</p>, treatment: <p>new copy</p> }}
      />
    ));
    expect(screen.getByText('new copy')).toBeInTheDocument();
  });

  test('falls back to `default` for an unmapped variant', () => {
    setup({ experiments: [] }, (
      <ExperimentSwitch name="missing" variants={{ default: <p>fallback</p> }} />
    ));
    expect(screen.getByText('fallback')).toBeInTheDocument();
  });
});
