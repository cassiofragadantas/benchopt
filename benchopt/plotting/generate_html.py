import json
import shutil
import webbrowser
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from mako.template import Template

from ..constants import PLOT_KINDS
from .plot_bar_chart import computeBarChartData  # noqa: F401
from .plot_objective_curve import compute_quantiles   # noqa: F401
from .plot_objective_curve import get_solver_color   # noqa: F401
from .plot_objective_curve import get_solver_marker  # noqa: F401

ROOT = Path(__file__).parent / "html"
DEFAULT_HTML_DIR = Path("html")
OUTPUTS = "outputs"
FIGURES = "figures"

TEMPLATE_INDEX = ROOT / "templates" / "index.mako.html"
TEMPLATE_BENCHMARK = ROOT / "templates" / "benchmark.mako.html"
TEMPLATE_RESULT = ROOT / "templates" / "result.mako.html"

SYS_INFO = {
    "main": [('system-cpus', 'cpu'),
             ('system-ram (GB)', 'ram (GB)'),
             ("version-cuda", 'cuda')
             ],
    "sub": [('platform', 'platform'),
            ('system-processor', 'processor'),
            ('env-OMP_NUM_THREADS', 'nb threads')
            ],
    "ter": [("version-numpy", "numpy"),
            ("version-scipy", "scipy")
            ]
}


def get_results(fnames, kinds, root_html, benchmark_name, copy=False):
    """Generate figures from a list of result files.

    Parameters
    ----------
    fnames : list of Path
        list of result files containing the benchmark results.
    kinds : list of str
        List of the kind of plots that will be generated. This needs to be a
        sub-list of PLOT_KINDS.keys().
    root_html : Path
        Directory where all the HTML files related to the benchmark are stored.
    benchmark_name : str
        Name of the benchmark, to prefix all files.
    copy : bool (default: False)
        If set to True, copy each file in the root_html / OUTPUTS
        directory, to make sure it can be downloaded.

    Returns
    -------
    results : dict
        Dictionary containing all the info on each run and the links to the
        generated figures.
    """
    results = []
    out_dir = root_html / OUTPUTS

    for fname in fnames:
        print(f"Processing {fname}")

        if fname.suffix == '.parquet':
            df = pd.read_parquet(fname)
        else:
            df = pd.read_csv(fname)

        datasets = list(df['data_name'].unique())
        sysinfo = get_sysinfo(df)
        # Copy result file if necessary
        # and give a relative path for HTML page access
        if copy:
            fname_in_output = out_dir / f"{benchmark_name}_{fname.name}"
            shutil.copy(fname, fname_in_output)
            fname = fname_in_output
        fname = fname.absolute().relative_to(root_html.absolute())

        # Generate figures
        result = dict(
            fname=fname,
            fname_short=fname.name,
            datasets=datasets,
            sysinfo=sysinfo,
            dataset_names=df['data_name'].unique(),
            objective_names=df['objective_name'].unique(),
            obj_cols=[k for k in df.columns if k.startswith('objective_')
                      and k != 'objective_name'],
            kinds=list(kinds),
        )
        results.append(result)

    for result in results:
        html_file_name = f"{result['fname_short'].replace('.csv', '.html')}"
        html_file_name = f"{html_file_name.replace('.parquet', '.html')}"

        result['page'] = (
            f"{benchmark_name}_"
            f"{html_file_name}"
        )

        # JSON
        result['json'] = json.dumps(shape_datasets_for_html(df))

        # Assets
        assets = ['result.js', 'symbols.js', 'hover_index.css',
                  'utilities.css', 'arrow_left.svg', 'home.svg',
                  'download.svg']
        for asset in assets:
            with open(root_html / 'static' / asset) as asset_file:
                result[asset] = asset_file.read()

    return results


def shape_datasets_for_html(df):
    """Return a dictionary with plotting data for each dataset."""
    datasets_data = {}

    for dataset in df['data_name'].unique():
        datasets_data[dataset] = shape_objectives_for_html(df, dataset)

    return datasets_data


def shape_objectives_for_html(df, dataset):
    """Return a dictionary with plotting data for each objective."""
    objectives_data = {}

    for objective in df['objective_name'].unique():
        objectives_data[objective] = shape_objectives_columns_for_html(
            df, dataset, objective)

    return objectives_data


def shape_objectives_columns_for_html(df, dataset, objective):
    """Return a dictionary with plotting data for each objective column."""
    objective_columns_data = {}
    columns = [
        c for c in df.columns
        if c.startswith('objective_') and c != 'objective_name'
    ]

    for column in columns:
        df_filtered = df.query(
            "data_name == @dataset & objective_name == @objective"
        )
        objective_columns_data[column] = {
            'solvers': shape_solvers_for_html(df_filtered, column),
            # Values used in javascript to do computation
            'transformers': {
                'c_star': float(df_filtered[column].min() - 1e-10),
                'max_f_0': float(
                    df_filtered[df_filtered['stop_val'] == 1][column].max()
                )
            }
        }

    return objective_columns_data


def shape_solvers_for_html(df, objective_column):
    """Return a dictionary with plotting data for each solver."""
    solver_data = {}
    for solver in df['solver_name'].unique():
        df_filtered = df.query("solver_name == @solver")

        # remove infinite values
        df_filtered = df_filtered.replace([np.inf, -np.inf], np.nan)
        df_filtered = df_filtered.dropna(subset=[objective_column])

        q1, q9 = compute_quantiles(df_filtered)
        solver_data[solver] = {
            'scatter': {
                'x': df_filtered.groupby('stop_val')['time']
                                .median().tolist(),
                'y': df_filtered.groupby('stop_val')[objective_column]
                                .median().tolist(),
                'q1': q1.tolist(),
                'q9': q9.tolist(),
            },
            'bar': {
                **computeBarChartData(df, objective_column, solver)
            },
            'color': get_solver_color(solver),
            'marker': get_solver_marker(solver)
        }

    return solver_data


def get_sysinfo(df):
    """Get a dictionnary of the recorded system informations.

    System informations are sorted in 3 levels: main, sub and ter.
        - Main : cpu - ram - cuda.
            Displayed directly in the benchmark and result pages
            and can be filtered on.
        - Sub : platform - processor - number of threads.
            Displayed on click in the benchmark and results pages.
        - Ter : numpy - scipy.
            Displayed on click in the result page.

    Parameters
    ----------
    df : pandas.DataFrame
        recorded data from the Benchmark

    Returns
    -------
    sysinfo : dict
        Contains the three-level sytem informations.
    """

    def get_val(df, key):
        if key in df:
            if key == 'platform':
                return (
                    str(df["platform"].unique()[0]) +
                    str(df["platform-release"].unique()[0]) + "-" +
                    str(df["platform-architecture"].unique()[0])
                )
            else:
                df['version-numpy'] = df['version-numpy'].astype(str)
                val = df[key].unique()[0]
                if not pd.isnull(val):
                    return str(val)
                return ''
        else:
            return ''
    sysinfo = {
        level: {name: get_val(df, key) for key, name in keys}
        for level, keys in SYS_INFO.items()
    }
    return sysinfo


def render_index(benchmark_names, static_dir, len_fnames):
    """Render a result index home page for all rendered benchmarks.

    Parameters
    ----------
    benchmark_names : list of str
        A list of all benchmark names that have been rendered.
    len_fnames : list of int
        A list of the number of files in each benchmark.

    Returns
    -------
    rendered : str
        A str with the HTML code for the index page.
    """

    pretty_names = [name.replace("benchmark_", "").replace("_",
                                                           " ").capitalize()
                    for name in benchmark_names]
    pretty_names, len_fnames, benchmark_names = map(
        list, zip(*sorted(zip(pretty_names, len_fnames, benchmark_names),
                          reverse=False))
    )

    return Template(
        filename=str(TEMPLATE_INDEX), input_encoding="utf-8"
    ).render(
        benchmarks=benchmark_names,
        nb_total_benchs=len(benchmark_names),
        max_rows=15, static_dir=static_dir,
        last_updated=datetime.now(),
        pretty_names=pretty_names,
        len_fnames=len_fnames
    )


def render_benchmark(results, benchmark_name, static_dir, home='index.html'):
    """Render a page indexing all runs for one benchmark.

    Parameters
    ----------
    results : list of Path
        List of all the run available for this benchmark.
    benchmark_name : str
        Named of the rendered benchmark.
    static_dir : str
        Relative path from HTML root to the static files.
    home : str
        URL of the home page.

    Returns
    -------
    rendered : str
        A str with the HTML code for the benchmark page.
    """

    return Template(
        filename=str(TEMPLATE_BENCHMARK), input_encoding="utf-8"
    ).render(
        results=results,
        benchmark=benchmark_name,
        max_rows=15, nb_total_benchs=len(results),
        last_updated=datetime.now(),
        static_dir=static_dir, home=home
    )


def render_all_results(results, benchmark_name, static_dir, home='index.html'):
    """Create an html file containing the plots from a benchmark run.

    Parameters
    ----------
    results : list of dict
        List of all the run that have been rendered for this benchmark.
    benchmark_name : str
        Named of the rendered benchmark.
    static_dir : str
        Relative path from HTML root to the static files.
    home : str
        URL of the home page.

    Returns
    -------
    htmls : list of str
        A List of str contraining the HTML code for each benchmark run.
    """
    template = TEMPLATE_RESULT
    htmls = []
    for result in results:
        html = Template(
            filename=str(template),
            input_encoding="utf-8"
        ).render(
            result=result,
            benchmark=benchmark_name,
            static_dir=static_dir,
            home=home
        )
        htmls.append(html)
    return htmls


def copy_static(root_html=None):
    "Copy static files in the HTML output folder."
    if root_html is None:
        root_html = DEFAULT_HTML_DIR
    static_dir = root_html / 'static'
    if static_dir.exists():
        shutil.rmtree(static_dir)
    shutil.copytree(ROOT / 'static', static_dir)
    return static_dir.relative_to(root_html)


def _fetch_cached_run_list(new_results, benchmark_html):
    # Load/update and dump a cache of the previous new_results to maintain
    # an up to date list of runs.
    benchmark_html_cache = benchmark_html / 'cache_run_list.json'
    new_results = {
        str(r['fname']): {
            'fname': str(r['fname']), 'fname_short': str(r['fname_short']),
            'sysinfo': r['sysinfo'],
            'page': str(r['page']), 'datasets': r['datasets']
        } for r in new_results
    }
    if benchmark_html_cache.exists():
        results = json.loads(benchmark_html_cache.read_text())
    else:
        results = {}
    results.update(new_results)
    benchmark_html_cache.write_text(json.dumps(results))

    return list(results.values())


def plot_benchmark_html(fnames, benchmark, kinds, display=True):
    """Plot a given benchmark as an HTML report. This function can either plot
    a single run or multiple ones.

    Parameters
    ----------
    fnames : list of Path or Path
        Name of the file in which the results are saved.
    benchmark : benchopt.Benchmark object
        Object to represent the benchmark.
    kinds : list of str
        List of the kind of plots that will be generated. This needs to be a
        sub-list of PLOT_KINDS.keys().
    display : bool
        If set to True, display the curves by opening
        the default browser.

    Returns
    -------
    None
    """
    if isinstance(fnames, Path):
        fnames = [fnames]

    # Get HTML folder for the benchmark and make sure it contains
    # figures directory and static files.
    root_html = benchmark.get_output_folder()
    (root_html / FIGURES).mkdir(exist_ok=True)
    static_dir = copy_static(root_html)
    bench_index = (root_html / benchmark.name).with_suffix('.html')
    home = bench_index.relative_to(root_html)

    # Create the figures and render the page as a html.
    results = get_results(fnames, kinds, root_html, benchmark.name)
    htmls = render_all_results(
        results, benchmark.name, static_dir=static_dir, home=home
    )

    # Save the resulting page in the HTML folder
    for result, html in zip(results, htmls):
        result_filename = root_html / result['page']
        print(f"Writing results to {result_filename}")
        with open(result_filename, "w", encoding="utf-8") as f:
            f.write(html)

    # Fetch run list from the benchmark and update the benchmark front page.
    run_list = _fetch_cached_run_list(results, root_html)
    rendered = render_benchmark(
        run_list, benchmark.name, static_dir=static_dir, home=home
    )
    print(f"Writing {benchmark.name} results to {bench_index}")
    with open(bench_index, "w", encoding="utf-8") as f:
        f.write(rendered)

    print("Rendering benchmark results...")
    # Display the file in the default browser
    if display:
        result_filename = (root_html / results[-1]['page']).absolute()
        webbrowser.open_new_tab('file://' + str(result_filename))


def plot_benchmark_html_all(patterns=(), benchmarks=(), root=None,
                            display=True):
    """Generate a HTML report for multiple benchmarks.

    This utility is the one used to create https://benchopt.github.io/results.
    It will open all benchmarks in `root` and create a website to browse them.

    Parameters
    ----------
    patterns : tuple of str
        Only include result files that match the provided patterns.
    benchmarks : tuple of Path
        Explicitly provides the benchmarks that should be display in the
        report.
    root : Path | None
        If no benchmarks is provided, list all directory in root that have
        a `outputs` folder and generate the report.
    display : bool
        If set to True, open the HTML report in default browser.

    Returns
    -------
    None
    """
    # Parse the arguments adn get the list of benchmarks and patterns.
    if not benchmarks:
        root = Path(root)
        benchmarks = [
            f for f in root.iterdir()
            if f.is_dir() and (f / 'outputs').is_dir()
        ]
    else:
        benchmarks = [Path(b) for b in benchmarks]
    if not patterns:
        patterns = ['*']

    # make sure the `html` folder exists and copy static files.
    root_html = DEFAULT_HTML_DIR
    (root_html / FIGURES).mkdir(exist_ok=True, parents=True)
    (root_html / OUTPUTS).mkdir(exist_ok=True, parents=True)
    static_dir = copy_static()

    # Loop over all benchmarks to
    len_fnames = []
    for benchmark in benchmarks:
        print(f'Rendering benchmark: {benchmark}')

        fnames = []
        for p in patterns:
            fnames += list(
                (benchmark / 'outputs').glob(f"{p}.parquet")
            ) + list((benchmark / 'outputs').glob(f"{p}.csv"))
        fnames = sorted(set(fnames))
        results = get_results(
            fnames, PLOT_KINDS.keys(), root_html, benchmark.name, copy=True
        )
        len_fnames.append(len(fnames))
        if len(results) > 0:
            rendered = render_benchmark(
                results, benchmark.name, static_dir=static_dir
            )

            benchmark_filename = (
                root_html / benchmark.name
            ).with_suffix('.html')
            print(f"Writing {benchmark.name} results to {benchmark_filename}")
            with open(benchmark_filename, "w") as f:
                f.write(rendered)

        htmls = render_all_results(
            results, benchmark.name, static_dir=static_dir
        )
        for result, html in zip(results, htmls):
            result_filename = root_html / result['page']
            print(f"Writing results to {result_filename}")
            with open(result_filename, "w", encoding="utf-8") as f:
                f.write(html)

    # Create an index that lists all benchmarks.
    rendered = render_index([b.name for b in benchmarks], static_dir,
                            len_fnames)
    index_filename = DEFAULT_HTML_DIR / 'index.html'
    print(f"Writing index to {index_filename}")
    with open(index_filename, "w", encoding="utf-8") as f:
        f.write(rendered)

    # Display the file in the default browser
    if display:
        webbrowser.open_new_tab('file://' + str(index_filename.absolute()))
