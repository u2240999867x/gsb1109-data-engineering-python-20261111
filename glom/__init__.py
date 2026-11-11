from glom.core import (glom,
                       Fill,
                       Auto,
                       register,
                       register_op,
                       Glommer,
                       Call,
                       Invoke,
                       Spec,
                       Ref,
                       SKIP,
                       STOP,
                       UP,
                       ROOT,
                       MODE,
                       Path,
                       Vars,
                       Val,
                       Coalesce,
                       Inspect,
                       Pipe,
                       GlomError,
                       BadSpec,
                       PathAccessError,
                       PathAssignError,
                       CoalesceError,
                       UnregisteredTarget,
                       T, S, A)

from glom.reduction import Sum, Fold, Flatten, flatten, FoldError, Merge, merge
from glom.matching import (M,
                           Or,
                           And,
                           Not,
                           Match,
                           MatchError,
                           TypeMatchError,
                           Regex,
                           Optional,
                           Required,
                           Switch,
                           Check,
                           CheckError)
from glom.mutation import Assign, Delete, assign, delete, PathDeleteError

# pure-data, configuration-safe spec loading (no source strings or imports).
# DataSpecError stays in glom.spec_data because it is a load-time validation
# error rather than a glom.glom() runtime error.
from glom.spec_data import (DataSpecRegistry,
                            load_data_spec,
                            loads_data_spec,
                            load_data_spec_file,
                            register_data_op)

# there's no -ion word that really fits what "streaming" means.
# generation, production, iteration, all have more relevant meanings
# elsewhere. (maybe procrastination :P)
from glom.streaming import Iter

from glom._version import __version__
